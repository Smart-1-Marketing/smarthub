/* The audio decoder, the dead air cutter, and one WAV encoder.
 *
 * There is no ffmpeg, pydub or numpy in this Hub's runtime, which is the
 * constraint every audio decision here is shaped around. What there IS, in
 * every browser this tool is used in, is a complete audio pipeline: the Web
 * Audio API decodes MP3, M4A/AAC, OGG, WebM and WAV to raw samples, and that
 * is enough to do all three jobs honestly.
 *
 * **Decoding.** `decode()` is the "audio decoder that recognizes the uploaded
 * file" — whatever format somebody drags in, it comes back as samples, and its
 * duration is a reading of the audio rather than a guess from the byte count
 * or the word count. Re-encoded to WAV on the way to the server, it turns
 * "not measured" into measured for every upload, because a WAV states its own
 * length in its header and `hub/radio_spec.wav_seconds()` can read it.
 *
 * **Dead air.** A read that runs long has two things wrong with it and only
 * one of them is the performance. `trim()` takes the silence out first,
 * because that is free and nobody can hear it go — a :32 read with six
 * half-second gaps is a :29 read somebody recorded with pauses. Only when that
 * is not enough is anybody asked to spend a take or a semitone on speed. The
 * thresholds are `hub/radio_spec.DEAD_AIR_DEFAULTS`, served to the page rather
 * than written here, so the advanced controls and the server agree.
 *
 * **One WAV encoder.** There were two, one in each radio builder's template,
 * and a third would have arrived with the next tool. `toWav()` is the copy
 * they all use.
 *
 * Nothing in here is destructive: every function returns new audio and the
 * caller keeps the original, which is what makes "use the original instead" a
 * button rather than a re-upload.
 */
(function (global) {
  'use strict';

  /* Resolved from the real global rather than from whatever this module was
     exported onto. Under CommonJS `global` above is `module.exports`, so
     reading the constructor off it finds nothing and the cutter cannot be
     driven in node -- which is where this repo tests its audio code, against
     the server's own WAV probe. */
  function audioGlobal() {
    if (typeof globalThis !== 'undefined' && globalThis.OfflineAudioContext) return globalThis;
    if (typeof globalThis !== 'undefined' && globalThis.webkitOfflineAudioContext) return globalThis;
    return global;
  }

  function ctxFor(channels, length, rate) {
    var scope = audioGlobal();
    var OAC = scope.OfflineAudioContext || scope.webkitOfflineAudioContext;
    if (!OAC) throw new Error('This browser cannot decode audio, so the '
      + 'recording cannot be measured or trimmed here. Chrome, Edge, Firefox '
      + 'and Safari all can.');
    return new OAC(channels, Math.max(1, Math.round(length)), rate);
  }

  /* Raw samples from whatever somebody handed us. Rejects by NAME: a file the
     browser will not decode is a file to convert, and "nothing happened" is
     the answer this Hub keeps having to undo. */
  function decode(arrayBuffer) {
    return Promise.resolve().then(function () {
      var probe = ctxFor(1, 1024, 44100);
      return probe.decodeAudioData(arrayBuffer);
    }).catch(function (err) {
      throw new Error('That file could not be decoded as audio'
        + (err && err.message ? ' (' + err.message + ')' : '')
        + '. MP3, M4A, WAV, OGG and WebM all work.');
    });
  }

  function decodeFile(file) {
    return file.arrayBuffer().then(decode);
  }

  /* Peak level per analysis window, in dBFS. One pass, mono-summed: a gap is a
     gap on every channel at once, and judging channels separately would keep a
     stereo hiss alive on one side and cut it on the other. */
  function envelope(buffer, windowMs) {
    var rate = buffer.sampleRate;
    var win = Math.max(1, Math.round((windowMs / 1000) * rate));
    var frames = Math.ceil(buffer.length / win);
    var out = new Float32Array(frames);
    for (var c = 0; c < buffer.numberOfChannels; c++) {
      var data = buffer.getChannelData(c);
      for (var f = 0; f < frames; f++) {
        var start = f * win, end = Math.min(buffer.length, start + win), peak = out[f];
        for (var i = start; i < end; i++) {
          var v = Math.abs(data[i]);
          if (v > peak) peak = v;
        }
        out[f] = peak;
      }
    }
    return { peaks: out, windowSamples: win };
  }

  function dbfs(amplitude) {
    return amplitude > 0 ? 20 * Math.log10(amplitude) : -Infinity;
  }

  /* Every run of quiet longer than `min_gap_ms`, in samples. Head and tail are
     reported separately because they are not gaps between anything and are
     usually the biggest single win on a phone recording. */
  // The house numbers, for a caller that leaves one out. NaN here does not
  // throw -- it compares false against every sample and finds no gaps at all,
  // which looks exactly like a clean recording.
  var FALLBACK = { threshold_db: -45, min_gap_ms: 350, keep_ms: 180,
                   head_ms: 120, tail_ms: 200 };

  function setting(opts, key) {
    var v = Number((opts || {})[key]);
    return isFinite(v) ? v : FALLBACK[key];
  }

  function findGaps(buffer, opts) {
    var o = opts || {};
    var windowMs = 20;
    var env = envelope(buffer, windowMs);
    var floor = setting(o, 'threshold_db');
    var minFrames = Math.ceil((setting(o, 'min_gap_ms') / 1000) * buffer.sampleRate / env.windowSamples);
    var runs = [], start = -1;
    for (var f = 0; f < env.peaks.length; f++) {
      var quiet = dbfs(env.peaks[f]) <= floor;
      if (quiet && start < 0) start = f;
      if (!quiet && start >= 0) {
        if (f - start >= minFrames) runs.push([start, f]);
        start = -1;
      }
    }
    if (start >= 0 && env.peaks.length - start >= minFrames) runs.push([start, env.peaks.length]);

    var last = env.peaks.length;
    return runs.map(function (r) {
      return {
        start: r[0] * env.windowSamples,
        end: Math.min(buffer.length, r[1] * env.windowSamples),
        head: r[0] === 0,
        tail: r[1] >= last
      };
    });
  }

  /* The trim itself. Returns new audio plus what it did, because a cutter that
     silently shortens somebody's read is indistinguishable from one that
     mangled it -- the panel prints the count and the seconds saved. */
  function trim(buffer, opts) {
    var o = opts || {};
    var rate = buffer.sampleRate;
    var ms = function (v) { return Math.max(0, Math.round((v / 1000) * rate)); };
    var keep = ms(setting(o, 'keep_ms'));
    var head = ms(setting(o, 'head_ms'));
    var tail = ms(setting(o, 'tail_ms'));
    var gaps = findGaps(buffer, o);

    // Each gap becomes "copy the audio up to here, then this much silence".
    // A leading gap needs no special case: its `start` is 0, so the span
    // before it is empty and what survives is the head allowance alone --
    // which is exactly the rule, rather than an exception to it.
    var pieces = [], cursor = 0, closed = 0;
    gaps.forEach(function (g) {
      var allowed = g.head ? head : (g.tail ? tail : keep);
      var span = g.end - g.start;
      if (span <= allowed) return;              // already short enough
      pieces.push({ from: cursor, to: g.start, silence: allowed });
      cursor = g.end;
      closed++;
    });
    pieces.push({ from: cursor, to: buffer.length, silence: 0 });

    var total = pieces.reduce(function (n, p) {
      return n + Math.max(0, p.to - p.from) + p.silence; }, 0);
    if (!closed || total <= 0) {
      return { buffer: buffer, closed: 0, saved: 0, gaps: gaps.length, changed: false };
    }

    var out = ctxFor(buffer.numberOfChannels, total, rate).createBuffer(
      buffer.numberOfChannels, total, rate);
    for (var c = 0; c < buffer.numberOfChannels; c++) {
      var src = buffer.getChannelData(c), dst = out.getChannelData(c), at = 0;
      pieces.forEach(function (p) {
        var n = Math.max(0, p.to - p.from);
        if (n) { dst.set(src.subarray(p.from, p.to), at); at += n; }
        at += p.silence;                        // already zeroed
      });
    }
    return {
      buffer: out, closed: closed, gaps: gaps.length, changed: true,
      saved: Math.round((buffer.duration - out.duration) * 100) / 100
    };
  }

  /* Play a finished read faster, for the one case with no re-record to ask
     for. Resampling, so it comes back shorter AND higher -- the caller quotes
     the semitones, which `hub/radio_spec.speed_suggestion()` works out. */
  function resample(buffer, rate) {
    var speed = Number(rate);
    if (!(speed > 1)) return Promise.resolve(buffer);
    var length = Math.max(1, Math.round(buffer.length / speed));
    var ctx = ctxFor(buffer.numberOfChannels, length, buffer.sampleRate);
    var src = ctx.createBufferSource();
    src.buffer = buffer;
    src.playbackRate.value = speed;
    src.connect(ctx.destination);
    src.start(0);
    return ctx.startRendering();
  }

  /* The one WAV encoder. A WAV states its own sample rate, channel count and
     data length in its header, which is what lets the server measure what it
     stored rather than believe the page that sent it. */
  function toWav(buf) {
    var ch = buf.numberOfChannels, len = buf.length, rate = buf.sampleRate;
    var bytes = len * ch * 2, ab = new ArrayBuffer(44 + bytes), v = new DataView(ab);
    function w(o, str) { for (var i = 0; i < str.length; i++) v.setUint8(o + i, str.charCodeAt(i)); }
    w(0, 'RIFF'); v.setUint32(4, 36 + bytes, true); w(8, 'WAVE');
    w(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, ch, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * ch * 2, true); v.setUint16(32, ch * 2, true);
    v.setUint16(34, 16, true);
    w(36, 'data'); v.setUint32(40, bytes, true);
    var data = []; for (var c = 0; c < ch; c++) data.push(buf.getChannelData(c));
    var o = 44;
    for (var i = 0; i < len; i++) for (var c2 = 0; c2 < ch; c2++) {
      var x = Math.max(-1, Math.min(1, data[c2][i]));
      v.setInt16(o, x < 0 ? x * 0x8000 : x * 0x7FFF, true); o += 2;
    }
    return new Blob([ab], { type: 'audio/wav' });
  }

  global.AudioTrim = {
    decode: decode, decodeFile: decodeFile, findGaps: findGaps,
    trim: trim, resample: resample, toWav: toWav, dbfs: dbfs
  };
})(typeof window !== 'undefined' ? window : this);
