import assert from "node:assert/strict";
import test from "node:test";

import {
  KEY, PAST, failedSources, load, localDate, save, select, staleSince, storage,
} from "../../site/history.js";

const repo = (name) => ({name});
const days = () => [
  {date: "2026-11-11", repos: [repo("a/new"), repo("b/top")]},
  {date: "2026-11-10", repos: [repo("c/old"), repo("d/old")]},
];
const names = (shown) => shown.map((d) => [d.date, d.repos.map((r) => r.name)]);

test("first visit shows only the latest day and marks the window", () => {
  const {shown, history} = select(days(), {}, "2026-11-11");
  assert.deepEqual(names(shown), [["2026-11-11", ["a/new", "b/top"]]]);
  assert.deepEqual(history, {
    "a/new": "2026-11-11", "b/top": "2026-11-11", "c/old": PAST, "d/old": PAST,
  });
});

test("same-day reload shows today's list again", () => {
  const first = select(days(), {}, "2026-11-11");
  const second = select(days(), first.history, "2026-11-11");
  assert.deepEqual(second.shown, first.shown);
  assert.deepEqual(second.history, first.history);
});

test("next day shows only new repos", () => {
  const first = select(days(), {}, "2026-11-11");
  const feed = [{date: "2026-11-12", repos: [repo("e/fresh"), repo("a/new")]}, ...days()];
  const {shown, history} = select(feed, first.history, "2026-11-12");
  assert.deepEqual(names(shown), [["2026-11-12", ["e/fresh"]]]);
  assert.equal(history["e/fresh"], "2026-11-12");
  assert.equal(history["a/new"], "2026-11-11");
});

test("a repo that left the window and trends again stays hidden", () => {
  const history = {"z/gone": "2026-11-01"};
  const feed = [{date: "2026-11-12", repos: [repo("z/gone")]}];
  const {shown} = select(feed, history, "2026-11-12");
  assert.deepEqual(shown, []);
});

test("names compare case-insensitively", () => {
  const history = {"a/new": "2026-11-01"};
  const feed = [{date: "2026-11-12", repos: [repo("A/New"), repo("B/Other")]}];
  const {shown, history: next} = select(feed, history, "2026-11-12");
  assert.deepEqual(names(shown), [["2026-11-12", ["B/Other"]]]);
  assert.equal(next["b/other"], "2026-11-12");
});

test("load survives corrupt and throwing storage", () => {
  assert.deepEqual(load({getItem: () => "{"}), {});
  assert.deepEqual(load({getItem: () => { throw new Error("denied"); }}), {});
  assert.deepEqual(load({getItem: () => null}), {});
  assert.deepEqual(load({getItem: () => "[1]"}), {});
  assert.deepEqual(load({getItem: () => '{"a/b":"2026-11-11"}'}), {"a/b": "2026-11-11"});
});

test("save writes under the key and swallows errors", () => {
  const store = {};
  save({setItem: (k, v) => { store[k] = v; }}, {"a/b": PAST});
  assert.equal(store[KEY], JSON.stringify({"a/b": PAST}));
  save({setItem: () => { throw new Error("full"); }}, {});
});

test("select does not mutate inputs", () => {
  const feed = days();
  const history = {"c/old": PAST};
  const feedCopy = structuredClone(feed);
  const historyCopy = structuredClone(history);
  select(feed, history, "2026-11-11");
  select(feed, {}, "2026-11-11");
  assert.deepEqual(feed, feedCopy);
  assert.deepEqual(history, historyCopy);
});

test("empty day groups are dropped", () => {
  const feed = [{date: "2026-11-11", repos: []}, ...days().slice(1)];
  const {shown} = select(feed, {x: PAST}, "2026-11-11");
  assert.deepEqual(names(shown), [["2026-11-10", ["c/old", "d/old"]]]);
});

test("localDate uses the local calendar date", () => {
  assert.equal(localDate(new Date(2026, 10, 5, 23, 59)), "2026-11-05");
});

test("null or throwing storage is tolerated", () => {
  assert.deepEqual(load(null), {});
  save(null, {});
  const broken = {
    getItem: () => { throw new Error("x"); },
    setItem: () => { throw new Error("x"); },
  };
  assert.deepEqual(load(broken), {});
  save(broken, {});
});

test("storage returns null when the global getter throws", () => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true, get() { throw new Error("SecurityError"); },
  });
  try {
    assert.equal(storage(), null);
  } finally {
    delete globalThis.localStorage;
  }
});

test("first visit skips an empty latest day", () => {
  const feed = [{date: "2026-11-12", repos: []}, ...days()];
  const {shown, history} = select(feed, {}, "2026-11-12");
  assert.deepEqual(names(shown), [["2026-11-11", ["a/new", "b/top"]]]);
  assert.deepEqual(history, {
    "a/new": "2026-11-12", "b/top": "2026-11-12", "c/old": PAST, "d/old": PAST,
  });
});

test("staleSince is null for a fresh feed", () => {
  const now = new Date("2026-11-11T12:00:00-03:00");
  const feed = {status: {ok: true, at: "2026-11-11T03:00:00-03:00"}, generated_at: null};
  assert.equal(staleSince(feed, now), null);
});

test("staleSince gives the last update when it is over 30 hours old", () => {
  const now = new Date("2026-11-11T12:00:00-03:00");
  const at = "2026-11-10T05:00:00-03:00";
  assert.equal(staleSince({status: {ok: true, at}}, now), at);
  assert.equal(staleSince({status: null, generated_at: at}, now), at);
});

test("staleSince ignores a missing or unreadable time", () => {
  const now = new Date("2026-11-11T12:00:00-03:00");
  assert.equal(staleSince({status: null, generated_at: null}, now), null);
  assert.equal(staleSince({}, now), null);
  assert.equal(staleSince({status: {at: "not a date"}}, now), null);
});

test("failedSources shows today's failures, a total outage included", () => {
  const failed = [{source: "trendshift", error: "x"}, {source: "github", error: "y"}];
  const feed = {last_fetch: "2026-11-10", generated_at: "2026-11-11T03:05:00-03:00",
                days: [{date: "2026-11-11", failed, repos: []}]};
  assert.deepEqual(failedSources(feed), failed);
});

test("failedSources hides an older day's failures once today was written", () => {
  const failed = [{source: "github", error: "boom"}];
  const days = [{date: "2026-11-10", failed, repos: []}];
  assert.deepEqual(failedSources({generated_at: "2026-11-11T06:00:00-03:00", days}), []);
});

test("failedSources is empty without generated_at or days", () => {
  const failed = [{source: "github", error: "boom"}];
  const days = [{date: "2026-11-11", failed, repos: []}];
  assert.deepEqual(failedSources({last_fetch: "2026-11-11", days}), []);
  assert.deepEqual(failedSources({generated_at: null, days}), []);
  assert.deepEqual(failedSources({generated_at: "2026-11-11T06:00:00-03:00", days: []}), []);
  assert.deepEqual(failedSources({}), []);
});
