/**
 * Defensive validation — the second line of defence behind
 * `hub/hyperframes.py`'s own `paint_params()`/`vox_params()` clamps. These
 * tests hand it exactly the shapes a well-behaved caller sends, and exactly
 * the shapes a malformed or hostile one might, and assert this service
 * never raises on either.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { validatePaintParams, validateVoxParams } from "../src/validate.js";

test("a normal paint request is accepted and cleaned", () => {
  const result = validatePaintParams({
    text: "Hello there",
    style: "paint_on",
    durationSeconds: 5,
    format: "9:16",
    brandColors: ["#f5c542", "not-a-color", "#2b6cb0"],
  });
  assert.ok("params" in result, JSON.stringify(result));
  if ("params" in result) {
    assert.equal(result.params.text, "Hello there");
    assert.equal(result.params.style, "paint_on");
    assert.equal(result.params.format, "9:16");
    assert.deepEqual(result.params.brandColors, ["#f5c542", "#2b6cb0"]);
  }
});

test("an unknown style falls back to the default rather than being refused", () => {
  const result = validatePaintParams({ text: "x", style: "watercolor" });
  assert.ok("params" in result);
  if ("params" in result) assert.equal(result.params.style, "handwriting");
});

test("duration is clamped into [1, 30]", () => {
  const tooLong = validatePaintParams({ text: "x", durationSeconds: 999 });
  assert.ok("params" in tooLong);
  if ("params" in tooLong) assert.equal(tooLong.params.durationSeconds, 30);

  const tooShort = validatePaintParams({ text: "x", durationSeconds: -5 });
  assert.ok("params" in tooShort);
  if ("params" in tooShort) assert.equal(tooShort.params.durationSeconds, 1);

  const notANumber = validatePaintParams({ text: "x", durationSeconds: "soon" });
  assert.ok("params" in notANumber);
  if ("params" in notANumber) assert.equal(notANumber.params.durationSeconds, 5);
});

test("with neither text nor an image, the request is refused", () => {
  const result = validatePaintParams({ text: "", imageUrl: "" });
  assert.ok("error" in result);
});

test("a non-http(s) imageUrl is refused rather than handed to Chromium", () => {
  const result = validatePaintParams({ text: "x", imageUrl: "file:///etc/passwd" });
  assert.ok("error" in result, "a file:// URL must not reach the page");
});

test("a body that is not an object is refused, not thrown on", () => {
  assert.ok("error" in validatePaintParams("just a string"));
  assert.ok("error" in validatePaintParams(null));
  assert.ok("error" in validatePaintParams(42));
});

test("a normal vox request is accepted with beats cleaned", () => {
  const result = validateVoxParams({
    title: "Why us",
    format: "16:9",
    beats: [
      { headline: "One", treatment: "statement", seconds: 8 },
      { headline: "Two", treatment: "not-a-real-treatment", seconds: 8 },
      { headline: "Three", treatment: "data", seconds: 8 },
      { headline: "Four", treatment: "quote", seconds: 8, source: "Somebody" },
    ],
  });
  assert.ok("params" in result, JSON.stringify(result));
  if ("params" in result) {
    assert.equal(result.params.beats.length, 4);
    assert.equal(result.params.beats[1].treatment, "collage"); // the closed
    // vocabulary falls back to the default rather than reaching the
    // template with a treatment it has no branch for.
  }
});

test("a beat with no headline is dropped, not fatal on its own", () => {
  const result = validateVoxParams({
    beats: [
      { headline: "", seconds: 8 },
      { headline: "One", seconds: 8 },
      { headline: "Two", seconds: 8 },
      { headline: "Three", seconds: 8 },
      { headline: "Four", seconds: 8 },
    ],
  });
  assert.ok("params" in result, JSON.stringify(result));
  if ("params" in result) assert.equal(result.params.beats.length, 4);
});

test("fewer than the minimum usable beats refuses the whole job", () => {
  const result = validateVoxParams({ beats: [{ headline: "Only one", seconds: 8 }] });
  assert.ok("error" in result);
});

test("beats is not even an array — refused, not thrown on", () => {
  const result = validateVoxParams({ beats: "not a list" });
  assert.ok("error" in result);
});

test("more than the maximum beats is trimmed rather than refused", () => {
  const beats = Array.from({ length: 15 }, (_, i) => ({ headline: `Beat ${i}`, seconds: 6 }));
  const result = validateVoxParams({ beats });
  assert.ok("params" in result);
  if ("params" in result) assert.equal(result.params.beats.length, 10);
});
