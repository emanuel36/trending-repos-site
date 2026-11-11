// Per-visitor "seen" history kept in localStorage: {lowercase repo name: "YYYY-MM-DD"}.

export const KEY = "trending-repos-site:seen:v1";
export const PAST = "1970-01-01";

/** The browser's localStorage, or null when site data is blocked. */
export function storage() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Reads the history; missing, corrupt or unreadable storage gives an empty one. */
export function load(store) {
  try {
    const value = JSON.parse(store.getItem(KEY));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

/** Writes the history; storage errors are ignored. */
export function save(store, history) {
  try {
    store.setItem(KEY, JSON.stringify(history));
  } catch {
    // Private mode or full storage: the page still works, it just forgets.
  }
}

/**
 * Picks what to show. `days` is newest first. On a first visit only the newest day with repos is
 * shown and every other repo in the window is marked as long seen. Later, a repo shows
 * if it is new or was first shown today. Returns {shown, history} without mutating inputs.
 */
export function select(days, history, today) {
  const next = {...history};
  const first = Object.keys(history).length === 0;
  const shown = [];
  const latest = days.findIndex((day) => day.repos.length > 0);
  days.forEach((day, i) => {
    const repos = [];
    for (const repo of day.repos) {
      const key = repo.name.toLowerCase();
      const seen = next[key];
      if (first && i !== latest) {
        next[key] = PAST;
      } else if (seen === undefined || seen === today) {
        next[key] = today;
        repos.push(repo);
      }
    }
    if (repos.length) shown.push({date: day.date, repos});
  });
  return {shown, history: next};
}

/** The visitor's calendar date as YYYY-MM-DD. */
export function localDate(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export const STALE_HOURS = 30;

/**
 * When the feed's last update (`status.at`, else `generated_at`) is more than STALE_HOURS before
 * `now`, returns that ISO time; otherwise, or when it is missing or unreadable, null.
 */
export function staleSince(feed, now) {
  const at = feed.status?.at || feed.generated_at;
  const time = Date.parse(at ?? "");
  if (Number.isNaN(time)) return null;
  return now.getTime() - time > STALE_HOURS * 3600 * 1000 ? at : null;
}

/**
 * The failed sources of the newest day, when that day is the day of the last write (the date
 * part of `generated_at`, São Paulo time), so an old failure does not show as the current one
 * and a total outage, which does not move `last_fetch`, still shows; otherwise an empty list.
 */
export function failedSources(feed) {
  const latest = (feed.days || [])[0];
  const written = typeof feed.generated_at === "string" ? feed.generated_at.slice(0, 10) : null;
  return latest && written && latest.date === written ? latest.failed || [] : [];
}
