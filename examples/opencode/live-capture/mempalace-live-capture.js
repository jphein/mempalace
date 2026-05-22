// MemPalace live-capture plugin for OpenCode (daemon-routed setups).
//
// Place this file at one of:
//   ~/.config/opencode/plugins/mempalace-live-capture.js   (global, all projects)
//   <project>/.opencode/plugins/mempalace-live-capture.js  (per-project)
//
// What it does
// ------------
// On every `session.idle` event, it spawns the companion
// `capture-session.py` script (Python stdlib only) which:
//   1. Reads OpenCode's local SQLite session DB.
//   2. Extracts the role-pair transcript using the in-tree
//      OpenCodeSourceAdapter (RFC 002 contract).
//   3. POSTs the transcript to the daemon's /silent-save endpoint.
//
// Why not use option-K's opencode-plugin-mempalace?
// ------------------------------------------------
// It's broken for daemon-routed setups in two compounding ways
// (filed upstream as option-K#4 and option-K#5):
//   - Subscribes to `chat.message`, which OpenCode never publishes.
//   - Even when patched, calls `mempalace mine <local-dir>`, which the
//     remote daemon evaluates against its own filesystem (404).
//
// This plugin bypasses both bugs by doing the extraction client-side and
// POSTing drawer content directly. It is intentionally minimal:
//   - No state machine, no threshold counter — every `session.idle` POSTs.
//   - The daemon's silent-save endpoint deduplicates by entry hash, so
//     repeated POSTs of the same transcript do not create duplicate drawers.
//   - No dependency on the option-K plugin or its npm install.
//
// Requirements
// ------------
//   * MemPalace repo cloned somewhere (the script imports adapter helpers).
//   * Python 3.9+ on PATH (no extra pip deps).
//   * Env: PALACE_DAEMON_URL and PALACE_API_KEY set in the shell that
//     launches opencode (the plugin inherits process.env).
//
// Configure CAPTURE_SCRIPT below to point at your local checkout's
// capture-session.py, e.g. ~/Projects/memorypalace/examples/opencode/live-capture/capture-session.py
import { spawn } from 'child_process';
import { existsSync, mkdirSync, openSync } from 'fs';
import path from 'path';

const CAPTURE_SCRIPT =
    process.env.MEMPALACE_LIVE_CAPTURE_SCRIPT ||
    path.join(
        process.env.HOME || '',
        'Projects/memorypalace/examples/opencode/live-capture/capture-session.py'
    );

function _firstExistingPython() {
    const candidates = [
        process.env.MEMPALACE_PYTHON,
        path.join(process.env.HOME || '', 'Projects/memorypalace/.venv/bin/python3'),
        path.join(process.env.HOME || '', 'Projects/memorypalace/venv/bin/python3'),
        '/usr/bin/python3',
        '/usr/local/bin/python3',
        'python3',
    ];
    for (const c of candidates) {
        if (!c) continue;
        if (c === 'python3') return c; // PATH resolution
        try {
            if (existsSync(c)) return c;
        } catch (e) { /* ignore */ }
    }
    return 'python3';
}
const PYTHON = _firstExistingPython();

function log(...args) {
    // Plugin stdout/stderr surfaces in ~/.local/share/opencode/log/*.log as
    // `service=plugin path=<this file> ...`. Keep noise quiet by default.
    if (process.env.MEMPALACE_LIVE_CAPTURE_DEBUG) {
        console.warn('[mempalace-live-capture]', ...args);
    }
}

function captureSession(sessionID, cwd) {
    if (!sessionID) return;
    if (!existsSync(CAPTURE_SCRIPT)) {
        log(`capture script not found: ${CAPTURE_SCRIPT}`);
        return;
    }
    if (!process.env.PALACE_DAEMON_URL || !process.env.PALACE_API_KEY) {
        log('PALACE_DAEMON_URL / PALACE_API_KEY not set — skipping');
        return;
    }
    const args = ['--session-id', sessionID];
    if (cwd) {
        args.push('--cwd', cwd);
    }
    // Route the capture's stdout+stderr to a rotated log so failures aren't
    // invisible. Default location: ~/.local/share/opencode/mempalace-live-capture.log.
    const logFile =
        process.env.MEMPALACE_LIVE_CAPTURE_LOG ||
        path.join(
            process.env.HOME || '',
            '.local/share/opencode/mempalace-live-capture.log'
        );
    let outStream;
    try {
        mkdirSync(path.dirname(logFile), { recursive: true });
        outStream = openSync(logFile, 'a');
    } catch (e) {
        // If we can't open the log file, fall back to ignoring (capture
        // failures stay invisible but the session itself isn't blocked).
        outStream = 'ignore';
    }
    const child = spawn(PYTHON, [CAPTURE_SCRIPT, ...args], {
        env: process.env,
        // Detach so the plugin's event handler returns immediately; the
        // POST happens asynchronously. We don't await success because
        // opencode may dispose the plugin host before /silent-save returns
        // (especially in `opencode run` mode).
        detached: true,
        stdio: ['ignore', outStream, outStream],
    });
    child.unref();
    log(`spawned capture for ${sessionID} (pid=${child.pid})`);
}

export default async function mempalaceLiveCapturePlugin(input) {
    const cwd = input?.worktree || input?.directory || process.cwd();
    log(`loaded; cwd=${cwd}, capture=${CAPTURE_SCRIPT}, python=${PYTHON}`);

    // Per-session debounce. session.idle and session.status[idle] both fire
    // around the same moment for one logical idle event; we coalesce them
    // (and any reentry within DEBOUNCE_MS) into a single capture call.
    // The daemon would dedupe by entry-hash anyway, but reducing the spawn
    // count keeps the log + load lighter.
    const DEBOUNCE_MS = 1500;
    const lastCapture = new Map(); // sessionID -> timestamp

    return {
        event: async ({ event }) => {
            const t = event?.type;
            if (
                t === 'session.idle' ||
                t === 'session.deleted' ||
                (t === 'session.status' && event.properties?.status?.type === 'idle')
            ) {
                const sessionID =
                    event.properties?.sessionID ||
                    event.properties?.info?.sessionID ||
                    event.properties?.info?.id;
                if (!sessionID) return;
                const now = Date.now();
                const last = lastCapture.get(sessionID) || 0;
                if (now - last < DEBOUNCE_MS) {
                    log(`debounced ${t} for ${sessionID} (${now - last}ms since last)`);
                    return;
                }
                lastCapture.set(sessionID, now);
                captureSession(sessionID, cwd);
            }
        },
    };
}
