const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

// Execute the actual helper, with TypeScript's type-only import erased.
const source = fs.readFileSync(
  path.join(__dirname, "../lib/review-groups.ts"),
  "utf8",
);
const output = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
  },
}).outputText;
const loaded = { exports: {} };
new Function("module", "exports", output)(loaded, loaded.exports);
const { groupFindings, groupReviewItems } = loaded.exports;

function finding(id, frame = 0, overrides = {}) {
  return {
    id,
    file_id: "f1",
    rule_name: "face",
    type_label: "人脸",
    value: "",
    media: "video",
    source: "detector",
    frame_index: frame,
    frame_ts: frame / 25,
    frame_duration: 0.04,
    bbox: [10 + frame, 10, 50, 50],
    status: "pending",
    ...overrides,
  };
}

test("two simultaneous moving targets keep separate chains and original frame objects", () => {
  const original = [0, 1, 2].flatMap((frame) => [
    finding(`a${frame}`, frame),
    finding(`b${frame}`, frame, { bbox: [200 + frame, 10, 50, 50] }),
  ]);
  const input = [...original].reverse();
  const before = JSON.stringify(input);
  const groups = groupFindings(input);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    groups.map((g) => g.members.map((f) => f.id)),
    [
      ["a0", "a1", "a2"],
      ["b0", "b1", "b2"],
    ],
  );
  assert.equal(JSON.stringify(input), before);
  assert.ok(
    groups
      .flatMap((g) => g.members)
      .every((member) => original.includes(member)),
  );
});

test("missing frames and real time gaps split groups", () => {
  assert.equal(groupFindings([finding("a", 0), finding("b", 2)]).length, 2);
  assert.equal(
    groupFindings([finding("a", 0), finding("b", 1, { frame_ts: 2 })]).length,
    2,
  );
  assert.equal(
    groupFindings([
      finding("a", 0),
      finding("b", 1, { bbox: [200, 10, 50, 50] }),
    ]).length,
    2,
  );
});

test("different rules, sensitive values, files, tracks and sources cannot silently merge", () => {
  for (const override of [
    { rule_name: "phone" },
    { value: "different" },
    { file_id: "f2" },
    { track_index: 1 },
    { source: "manual" },
  ]) {
    assert.equal(
      groupFindings([finding("a", 0), finding("b", 1, override)]).length,
      2,
    );
  }
});

test("audio spans group only within a track and near time, preserving mixed states", () => {
  const a = finding("a", 0, {
    media: "audio",
    bbox: undefined,
    start: 1,
    end: 2,
    status: "accepted",
    value: "123",
  });
  const b = finding("b", 0, {
    media: "audio",
    bbox: undefined,
    start: 2.1,
    end: 3,
    value: "123",
  });
  const groups = groupFindings([b, a]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].status, "mixed");
  assert.deepEqual(groupReviewItems(groups[0], "rejected"), [
    { id: "a", status: "rejected" },
    { id: "b", status: "rejected" },
  ]);
  assert.equal(a.status, "accepted");
  assert.equal(b.status, "pending");
  assert.equal(groupFindings([a, { ...b, track_index: 1 }]).length, 2);
  assert.equal(groupFindings([a, { ...b, start: 2.3 }]).length, 2);
});

test("manual rectangles, images and text stay individually reviewable", () => {
  for (const override of [
    { source: "manual" },
    { media: "image" },
    {
      media: "text",
      bbox: undefined,
      frame_index: undefined,
      frame_ts: undefined,
      frame_duration: undefined,
    },
  ]) {
    const input = [finding("a", 0, override), finding("b", 1, override)];
    assert.equal(groupFindings(input).length, 2);
  }
});

test("a long continuous chain retains every frame exactly once", () => {
  const input = Array.from({ length: 2000 }, (_, frame) =>
    finding(`f${frame}`, frame, { bbox: [10, 10, 50, 50] }),
  );
  const groups = groupFindings(input);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].members.length, input.length);
  assert.equal(
    new Set(groups.flatMap((g) => g.members.map((f) => f.id))).size,
    input.length,
  );
  assert.deepEqual(
    groupReviewItems(groups[0], "accepted").map((f) => f.id),
    input.map((f) => f.id),
  );
});
