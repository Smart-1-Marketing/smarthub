/**
 * The one place this service shells out to ffmpeg. Two calls: encode a PNG
 * frame sequence into an MP4, optionally muxing an audio track over it, and
 * probe a finished file's duration. Nothing here trusts its own arithmetic
 * for the delivered duration — `probeDuration()` reads it back off the
 * actual file, the way `hub/radio_spec.wav_seconds()` insists on measuring
 * a WAV's length from its own bytes rather than trusting whoever made it.
 */

import { spawn } from "node:child_process";

interface RunResult {
  code: number;
  stdout: string;
  stderr: string;
}

function run(cmd: string, args: string[]): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => { stdout += d.toString(); });
    child.stderr.on("data", (d) => { stderr += d.toString(); });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? -1, stdout, stderr: stderr.slice(-4000) }));
  });
}

export interface EncodeOptions {
  framesDir: string;
  framePattern: string; // e.g. "frame_%05d.png"
  fps: number;
  outFile: string;
  audioUrl?: string; // optional http(s) URL, muxed over the video and
  // trimmed to the shorter of the two (-shortest) rather than extending the
  // clip to match narration that overran its own beat timing.
}

export async function encodeMp4(opts: EncodeOptions): Promise<void> {
  const inputPattern = `${opts.framesDir}/${opts.framePattern}`;
  const args = ["-y", "-framerate", String(opts.fps), "-i", inputPattern];
  if (opts.audioUrl) {
    args.push("-i", opts.audioUrl);
  }
  args.push("-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart");
  if (opts.audioUrl) {
    args.push("-c:a", "aac", "-shortest");
  }
  args.push(opts.outFile);

  const { code, stderr } = await run("ffmpeg", args);
  if (code !== 0) {
    throw new Error(`ffmpeg exited ${code}: ${stderr || "no output"}`);
  }
}

export async function probeDuration(file: string): Promise<number | null> {
  const { code, stdout } = await run("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    file,
  ]);
  if (code !== 0) return null;
  const value = parseFloat(stdout.trim());
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
}
