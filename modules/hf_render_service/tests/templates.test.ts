/**
 * The two template names are the join between this service and
 * `hub/hyperframes.py`, which refuses an unknown name *before* it ever
 * reaches here. This file pins the literal spellings against the ones a
 * human reading `hub/hyperframes.py`'s `TEMPLATES` dict would see, so a
 * rename on either side fails a test rather than turning into "the render
 * service is down" for every job.
 */
import test from "node:test";
import assert from "node:assert/strict";
import {
  TEMPLATE_NAMES,
  isKnownTemplate,
  dimensionsFor,
  FORMAT_DIMENSIONS,
} from "../src/templates.js";

test("the template names match hub/hyperframes.py's TEMPLATES exactly", () => {
  assert.deepEqual([...TEMPLATE_NAMES].sort(), ["paint-animation", "vox-explainer"]);
});

test("isKnownTemplate refuses anything not in the list", () => {
  assert.equal(isKnownTemplate("paint-animation"), true);
  assert.equal(isKnownTemplate("vox-explainer"), true);
  assert.equal(isKnownTemplate("paint_animation"), false); // the underscore
  // spelling is a style, not this template — a caller sending it should be
  // refused, not silently corrected.
  assert.equal(isKnownTemplate(""), false);
  assert.equal(isKnownTemplate("PAINT-ANIMATION"), false);
});

test("format dimensions match modules/commercial_builder/config.OUTPUT_FORMATS", () => {
  assert.deepEqual(dimensionsFor("16:9"), { width: 1920, height: 1080 });
  assert.deepEqual(dimensionsFor("9:16"), { width: 1080, height: 1920 });
  assert.deepEqual(dimensionsFor("1:1"), { width: 1080, height: 1080 });
});

test("an unknown format falls back to 16:9 rather than throwing", () => {
  assert.deepEqual(dimensionsFor("21:9"), FORMAT_DIMENSIONS["16:9"]);
  assert.deepEqual(dimensionsFor(""), FORMAT_DIMENSIONS["16:9"]);
});
