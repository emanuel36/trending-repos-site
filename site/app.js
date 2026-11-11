import {
  failedSources, load, localDate, save, select, staleSince, storage,
} from "./history.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const short = (n) => n == null ? "—"
  : n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, "") + "k" : String(n);
const day = (iso) => iso ? iso.slice(8, 10) + "/" + iso.slice(5, 7) : "—";
const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? u : null;
const SOURCE = {trendshift: "Trendshift", github: "GitHub Trending"};

const growth = (r) => r.gained == null ? "—" : `+${short(r.gained)} hoje`;
const sourceLabel = (r) => (SOURCE[r.source] || r.source) + (r.rank == null ? "" : ` #${r.rank}`);

function thumb(r) {
  const url = safeUrl(r.image);
  // An <img> attribute, unlike a CSS url(), takes the URL as it is, safely escaped.
  return url ? `<img class="thumb" src="${esc(url)}" alt="" loading="lazy">`
    : `<div class="thumb">sem imagens</div>`;
}

function tags(r) {
  const s = r.summary;
  return `<span class="tag src">${esc(sourceLabel(r))}</span>`
    + (s && s.translated_from
      ? `<span class="tag zh">README em ${esc(s.translated_from)} · traduzido</span>` : "")
    + (r.image ? "" : `<span class="tag noimg">sem imagens</span>`)
    + (s ? `<span class="tag">${esc(s.maturity)}</span>` : "");
}

function row(r, i, open) {
  const s = r.summary;
  const what = s ? s.what : (r.description || "Sem resumo.");
  const first = what.match(/^.*?[.!?](\s|$)/)?.[0] || what;
  const link = `<a href="https://github.com/${esc(r.name)}" target="_blank" rel="noopener">`
    + `${esc(r.name)}</a>`;
  const home = safeUrl(r.homepage);
  const meta = [r.language, r.license, `criado ${day(r.created)}`,
                `último commit ${day(r.pushed)}`, `${short(r.forks)} forks`,
                `${short(r.issues)} issues`].filter(Boolean).map(esc).join(" · ");
  const facts = s ? `<dl><dt>Para quem</dt><dd>${esc(s.who)}</dd>`
    + (s.alternatives ? `<dt>Alternativas</dt><dd>${esc(s.alternatives)}</dd>` : "")
    + `</dl>` : "";
  return `<details${open ? " open" : ""}><summary class="cols">
      <span class="rank">${i + 1}</span>${thumb(r)}
      <span><b>${link}</b><br><span class="meta">${esc(first)}</span></span>
      <span class="stars">★ ${short(r.stars)}</span><span class="up">${esc(growth(r))}</span>
      <span class="meta">${esc(SOURCE[r.source] || "")}</span>
    </summary><div class="more">
      <div><div>${tags(r)}</div><p>${esc(what)}</p>${facts}
        ${home ? `<p><a href="${esc(home)}" target="_blank" rel="noopener">${esc(home)}</a></p>`
          : ""}</div>
      <div>${thumb(r)}<div class="meta" style="margin-top:8px">${meta}</div></div>
    </div></details>`;
}

const set = (id, html) => document.getElementById(id).innerHTML = html;

const HEAD = `<div class="cols head"><span>#</span><span></span><span>Repositório</span>
  <span>Estrelas</span><span>Cresceu</span><span>Origem</span></div>`;

function heading(date, today) {
  const label = new Date(date + "T12:00:00")
    .toLocaleDateString("pt-BR", {day: "numeric", month: "long"});
  return `<h2>${date === today ? "Hoje · " : ""}${esc(label)}</h2>`;
}

function renderHeader(feed, shown) {
  const repos = shown.flatMap((d) => d.repos);
  const counts = {};
  repos.forEach((r) => counts[r.source] = (counts[r.source] || 0) + 1);
  const parts = [`${repos.length} repositórios`,
    ...Object.keys(SOURCE).filter((k) => counts[k]).map((k) => esc(`${counts[k]} ${SOURCE[k]}`))];
  failedSources(feed).forEach((f) => parts.push(
    `<span class="bad">${esc(SOURCE[f.source] || f.source)} falhou</span>`));
  if ((feed.pending || []).length > 0) parts.push(`${feed.pending.length} aguardando resumo`);
  set("sub", parts.join(" · "));
  const banners = [];
  const since = staleSince(feed, new Date());
  if (since) banners.push(`<div class="banner">sem atualização desde ${esc(day(since))}</div>`);
  const status = feed.status;
  if (status && !status.ok) {
    banners.push(`<div class="banner">última execução falhou em ${esc(day(status.at))}: `
      + `${esc(status.reason || "motivo desconhecido")}</div>`);
  }
  set("banner", banners.join(""));
}

/** Renders the page and returns the history to save; throws on a malformed feed. */
function render(feed, store) {
  const today = localDate(new Date());
  const {shown, history} = select(feed.days || [], load(store), today);
  renderHeader({...feed, days: feed.days || []}, shown);
  if (!shown.length) {
    set("content", `<p class="empty">Nada novo hoje.</p>`);
    return history;
  }
  let first = true;
  set("content", HEAD + shown.map((d) => heading(d.date, today) + d.repos.map((r, i) => {
    const html = row(r, i, first);
    first = false;
    return html;
  }).join("")).join(""));
  return history;
}

const failed = () => {
  set("banner", "");
  set("sub", "");
  set("content", `<p class="empty">Não foi possível carregar os dados.</p>`);
};

async function main() {
  let feed;
  try {
    const res = await fetch("data/feed.json", {cache: "no-store"});
    if (!res.ok) throw new Error(String(res.status));
    feed = await res.json();
  } catch {
    failed();
    return;
  }
  const store = storage();
  let history;
  try {
    history = render(feed, store);
  } catch {
    // A feed that parses but has the wrong shape: show the error and remember nothing.
    failed();
    return;
  }
  save(store, history);
}

main();
