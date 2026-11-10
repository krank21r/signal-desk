/* Signal Desk — dashboard client. Vanilla JS, no build step, no CDN. */

const state = {
  rows: [],
  errors: {},
  filter: "all",
  sort: { key: null, dir: -1 },
  selected: null,
  detail: null,
  view: "signals",
  alerts: [],
  alertDays: 30,
  alertsLoading: false,
  alertsError: null,
  trades: [],
  tradeSummary: {},
  loggedSources: new Set(),
  loading: false,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ------------------------------------------------------------------ utils */

const nf = (v, digits) =>
  v == null || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

const fmtPrice = (v) => {
  if (v == null) return "—";
  if (Math.abs(v) >= 1000) return nf(v, 2);
  if (Math.abs(v) >= 1) return nf(v, 2);
  if (Math.abs(v) >= 0.01) return nf(v, 4);
  return v.toFixed(6);
};

const fmtPct = (v, digits = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${nf(v, digits)}%`);
const toneOf = (v) => (v == null ? "dim" : v > 0 ? "pos" : v < 0 ? "neg" : "dim");
const fmtDate = (unix) =>
  new Date(unix * 1000).toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });

async function getJSON(url) {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

async function sendJSON(url, body, method = "POST") {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const payload = await res.json();
      if (payload && payload.detail) detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    } catch (_) {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json();
}

/* ----------------------------------------------------------------- theme */

const THEME_KEY = "signaldesk.theme";

function systemPrefersLight() {
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches);
}

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const button = $("#theme");
  if (button) {
    const goesToLight = theme === "dark";
    button.setAttribute("aria-pressed", String(theme === "light"));
    button.setAttribute("aria-label", goesToLight ? "Switch to light theme" : "Switch to dark theme");
    button.title = button.getAttribute("aria-label");
    button.textContent = goesToLight ? "☀" : "☾";
  }
  const meta = document.getElementById("themeColor");
  if (meta) meta.setAttribute("content", theme === "light" ? "#f5f6f8" : "#08090c");
}

function readSavedTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return saved === "light" || saved === "dark" ? saved : null;
  } catch (_) {
    return null;
  }
}

function initTheme() {
  applyTheme(readSavedTheme() || (systemPrefersLight() ? "light" : "dark"));
  if (window.matchMedia) {
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = (event) => {
      if (readSavedTheme()) return; // an explicit choice wins over the OS
      applyTheme(event.matches ? "light" : "dark");
      redrawCharts();
    };
    if (query.addEventListener) query.addEventListener("change", onChange);
    else if (query.addListener) query.addListener(onChange);
  }
}

function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (_) {
    /* private mode: the choice just will not persist */
  }
  applyTheme(next);
  redrawCharts();
}

function redrawCharts() {
  // SVG strokes are baked in at render time, so re-render the open drawer.
  if (state.detail && !$("#drawer").hidden) renderDrawer(state.detail);
}

/* --------------------------------------------------------------- changes */

const SNAP_KEY = "signaldesk.snapshot";

function readSnapshot() {
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== 1 || !parsed.signals) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

function writeSnapshot(rows) {
  const signals = {};
  rows.forEach((row) => {
    if (row.signal) signals[row.symbol] = row.signal;
  });
  try {
    localStorage.setItem(SNAP_KEY, JSON.stringify({ v: 1, at: new Date().toISOString(), signals }));
  } catch (_) {
    /* storage blocked: the banner simply will not persist */
  }
}

function diffSignals(snapshot, rows) {
  if (!snapshot || !snapshot.signals) return [];
  const changes = [];
  rows.forEach((row) => {
    const before = snapshot.signals[row.symbol];
    if (!before || before === row.signal) return;
    changes.push({
      symbol: row.symbol,
      from: before,
      to: row.signal,
      flipped: before !== "FLAT" && row.signal !== "FLAT",
    });
  });
  // A long-to-short reversal matters more than entering or leaving flat.
  changes.sort((a, b) => Number(b.flipped) - Number(a.flipped));
  return changes;
}

function hideBanner() {
  const banner = $("#banner");
  if (!banner) return;
  banner.hidden = true;
  banner.replaceChildren();
}

function renderBanner(changes, sinceIso) {
  const banner = $("#banner");
  if (!banner) return;
  if (!changes.length) return hideBanner();
  const since = new Date(sinceIso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
  const items = changes
    .map((change) => {
      const tone = change.to === "LONG" ? "pos" : change.to === "SHORT" ? "neg" : "dim";
      return `<button class="chg" type="button" data-symbol="${change.symbol}">
        <span class="chg-sym">${change.symbol}</span>
        <span class="chg-from">${change.from}</span><span class="chg-arrow">→</span>
        <span class="${tone}">${change.to}</span>
      </button>`;
    })
    .join("");
  banner.innerHTML = `<span class="banner-label">${changes.length} change${
    changes.length === 1 ? "" : "s"
  } since ${since}</span><div class="banner-items">${items}</div>
    <button class="btn banner-dismiss" id="bannerDismiss" type="button">Got it</button>`;
  banner.hidden = false;
}

function acknowledgeChanges() {
  writeSnapshot(state.rows);
  hideBanner();
}

/* ----------------------------------------------------------------- alerts */

function signalText(value) {
  const tone = value === "LONG" ? "pos" : value === "SHORT" ? "neg" : "dim";
  return `<span class="${tone}">${value}</span>`;
}

function alertMatches(row) {
  const term = $("#search").value.trim().toUpperCase();
  if (state.filter !== "all" && row.kind !== state.filter) return false;
  if (term && !row.symbol.includes(term) && !row.name.toUpperCase().includes(term)) return false;
  return true;
}

function renderAlerts() {
  const tbody = $("#alertRows");
  if (!tbody) return;
  tbody.replaceChildren();

  if (state.alertsError) {
    tbody.innerHTML = `<tr><td colspan="9" class="errorbar">${state.alertsError}</td></tr>`;
    return;
  }

  const rows = state.alerts.filter(alertMatches);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">No signal changes in this window.</td></tr>`;
    return;
  }

  const frag = document.createDocumentFragment();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    tr.dataset.symbol = row.symbol;
    tr.setAttribute("role", "button");
    tr.setAttribute("aria-label", `${row.symbol} ${row.from} to ${row.to} on ${row.date}`);
    const source = `${row.symbol}:${row.date}:${row.to}`;
    const logged = state.loggedSources.has(source);
    const action =
      row.to === "FLAT"
        ? `<span class="dim">—</span>`
        : `<button class="mini-btn${logged ? " is-logged" : ""}" type="button" data-log="${source}"${
            logged ? " disabled" : ""
          }>${logged ? "logged ✓" : "I took it"}</button>`;
    const cells = [
      `<span class="symcell"><span class="ticker">${row.symbol}</span><span class="nick">${row.name}</span></span>`,
      `${signalText(row.from)} <span class="dim">→</span> ${signalText(row.to)}`,
      row.date,
      `<span title="${row.bars_ago} bars">${row.days_ago === 0 ? "today" : `${row.days_ago}d`}</span>`,
      fmtPrice(row.price),
      fmtPrice(row.price_now),
      `<span class="${toneOf(row.move_since)}">${fmtPct(row.move_since)}</span>`,
      signalText(row.signal_now),
      action,
    ];
    tr.innerHTML = cells
      .map((html, i) => `<td${i === 0 ? ' class="col-sym"' : ""}>${html}</td>`)
      .join("");
    tr.addEventListener("click", (event) => {
      if (event.target.closest("button")) return; // the Log button handles its own click
      openDetail(row.symbol);
    });
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetail(row.symbol);
      }
    });
    frag.appendChild(tr);
  });
  tbody.appendChild(frag);
}

async function loadAlerts() {
  if (state.alertsLoading) return;
  state.alertsLoading = true;
  const badge = $("#alertBadge");
  try {
    const data = await getJSON(`/api/alerts?asset_class=all&days=${state.alertDays}`);
    state.alerts = data.alerts || [];
    state.alertsError = null;
    const counts = data.counts || {};
    if (badge) {
      badge.textContent = counts.total ?? 0;
      badge.hidden = !counts.total;
    }
    const summary = $("#alertSummary");
    if (summary) {
      summary.textContent = `${counts.total ?? 0} change${counts.total === 1 ? "" : "s"} in ${
        counts.window_days ?? state.alertDays
      } days · ${counts.flips ?? 0} flips · ${counts.entries ?? 0} entries · ${counts.exits ?? 0} exits · newest first`;
    }
  } catch (error) {
    state.alerts = [];
    state.alertsError = `Could not load alerts: ${error.message}`;
    if (badge) badge.hidden = true;
    const summary = $("#alertSummary");
    if (summary) summary.textContent = "unavailable";
  } finally {
    state.alertsLoading = false;
    renderAlerts();
  }
}

/* ------------------------------------------------------------------ trades */

async function loadTrades() {
  try {
    const data = await getJSON("/api/trades");
    state.trades = data.trades || [];
    state.tradeSummary = data.summary || {};
    state.loggedSources = new Set(state.trades.map((trade) => trade.source).filter(Boolean));

    const badge = $("#tradeBadge");
    const open = state.tradeSummary.open || 0;
    if (badge) {
      badge.textContent = open;
      badge.hidden = !open;
    }

    const summary = $("#tradeSummary");
    if (summary) {
      if (!state.trades.length) {
        summary.textContent = "No trades logged yet — use “I took it” on a Trade alert.";
      } else {
        const parts = [`${open} open`, `${state.tradeSummary.closed || 0} closed`];
        if (state.tradeSummary.open_r != null) {
          parts.push(`open ${state.tradeSummary.open_r >= 0 ? "+" : ""}${nf(state.tradeSummary.open_r, 2)}R`);
        }
        if (state.tradeSummary.closed_win_rate != null) {
          parts.push(`closed win ${nf(state.tradeSummary.closed_win_rate, 0)}%`);
        }
        summary.textContent = parts.join(" · ");
      }
      if (data.storage && data.storage.persistent === false) {
        summary.textContent += " · ephemeral storage: trades reset when the server restarts";
      }
    }

    renderTrades();
    if (state.view === "alerts") renderAlerts(); // refresh the logged state on the buttons
  } catch (error) {
    const summary = $("#tradeSummary");
    if (summary) summary.textContent = `Could not load trades: ${error.message}`;
  }
}

function renderTrades() {
  const tbody = $("#tradeRows");
  if (!tbody) return;
  tbody.replaceChildren();

  if (!state.trades.length) {
    tbody.innerHTML = `<tr><td colspan="11" class="empty">No trades logged yet. Open Trade alerts and hit “I took it” on an entry.</td></tr>`;
    return;
  }

  const frag = document.createDocumentFragment();
  state.trades.forEach((trade) => {
    const tr = document.createElement("tr");
    const sideTone = trade.side === "LONG" ? "pos" : "neg";
    const cells = [
      `<button class="link-sym" type="button" data-open-symbol="${trade.symbol}">${trade.symbol}</button>`,
      `<span class="${sideTone}">${trade.side}</span>`,
      (trade.opened_at || "").slice(0, 10),
      trade.entry != null ? fmtPrice(trade.entry) : `<span class="dim">—</span>`,
      trade.mark != null ? fmtPrice(trade.mark) : `<span class="dim">—</span>`,
      `<span class="${toneOf(trade.pnl_pct)}">${fmtPct(trade.pnl_pct, 2)}</span>`,
      `<span class="${toneOf(trade.r_multiple)}">${trade.r_multiple != null ? nf(trade.r_multiple, 2) : "—"}</span>`,
      trade.stop != null ? fmtPrice(trade.stop) : `<span class="dim">—</span>`,
      trade.target != null ? fmtPrice(trade.target) : `<span class="dim">—</span>`,
      `<span class="${trade.status === "open" ? "pos" : "dim"}">${trade.status}</span>`,
      trade.status === "open"
        ? `<button class="mini-btn" type="button" data-close="${trade.id}">Close</button><button class="mini-btn" type="button" data-delete="${trade.id}" aria-label="Delete trade">×</button>`
        : `<button class="mini-btn" type="button" data-delete="${trade.id}" aria-label="Delete trade">×</button>`,
    ];
    tr.innerHTML = cells
      .map((html, i) => `<td${i === 0 ? ' class="col-sym"' : ""}>${html}</td>`)
      .join("");
    frag.appendChild(tr);
  });
  tbody.appendChild(frag);
}

async function logTrade(row, button) {
  if (!button || button.disabled) return;
  button.disabled = true;
  button.textContent = "saving…";
  const live = state.rows.find((item) => item.symbol === row.symbol) || {};
  // Levels are recomputed at the price actually paid, using the same 2xATR stop
  // and 3xATR target as the engine (config.STOP_ATR / TARGET_ATR); reusing an
  // older position's stop would misstate the risk this trade is taking.
  const side = row.to === "LONG" ? 1 : -1;
  let stop = live.stop ?? null;
  let target = live.target ?? null;
  if (row.price_now && live.atr_pct) {
    const atr = (row.price_now * live.atr_pct) / 100;
    stop = row.price_now - side * 2 * atr;
    target = row.price_now + side * 3 * atr;
  }
  try {
    await sendJSON("/api/trades", {
      symbol: row.symbol,
      side: row.to,
      entry: row.price_now,
      stop,
      target,
      source: `${row.symbol}:${row.date}:${row.to}`,
      note: `alert ${row.from} → ${row.to} on ${row.date} at ${fmtPrice(row.price)}`,
    });
    await loadTrades();
  } catch (error) {
    button.disabled = false;
    button.textContent = "failed";
    setTimeout(() => {
      button.textContent = "I took it";
    }, 2500);
    console.error("log trade failed", error);
  }
}

async function closeTrade(id) {
  try {
    await sendJSON(`/api/trades/${id}/close`, {});
    await loadTrades();
  } catch (error) {
    console.error("close trade failed", error);
  }
}

async function deleteTrade(id) {
  if (!window.confirm("Delete this trade from the journal?")) return;
  try {
    await sendJSON(`/api/trades/${id}`, undefined, "DELETE");
    await loadTrades();
  } catch (error) {
    console.error("delete trade failed", error);
  }
}

function renderActiveView() {
  if (state.view === "alerts") renderAlerts();
  else if (state.view === "trades") renderTrades();
  else renderTable();
}

function switchView(name, pushHash = true) {
  state.view = name === "alerts" || name === "trades" ? name : "signals";
  $$(".view").forEach((button) => {
    const active = button.dataset.view === state.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $("#signalsView").hidden = state.view !== "signals";
  $("#alertsView").hidden = state.view !== "alerts";
  $("#tradesView").hidden = state.view !== "trades";
  $("#counts").hidden = state.view !== "signals";
  if (pushHash) {
    const suffix = state.view === "signals" ? "" : `#${state.view}`;
    history.replaceState(null, "", `${location.pathname}${suffix}`);
  }
  if (state.view === "alerts") loadAlerts();
  if (state.view === "trades") loadTrades();
  renderActiveView();
}

/* ------------------------------------------------------------------ table */

function signalChip(row) {
  const cls = row.signal === "LONG" ? "long" : row.signal === "SHORT" ? "short" : "flat";
  const lean =
    row.signal === "FLAT" && row.bias && row.bias !== "neutral"
      ? `<span class="lean">${row.bias === "lean_long" ? "◮" : "◭"} lean ${row.bias === "lean_long" ? "long" : "short"}</span>`
      : "";
  const warn = row.blocked
    ? `<span class="warn" title="${row.block_reason || "regime gate"}">▲</span>`
    : "";
  return `<span class="chip ${cls}">${row.signal}</span>${lean}${warn}`;
}

function scoreCell(score) {
  const magnitude = Math.min(Math.abs(score || 0) / 4.5, 1) * 50;
  const side = score > 0 ? "long" : "short";
  return `<span class="meter"><i class="${side}" style="width:${magnitude}%"></i></span>${nf(score, 2)}`;
}

function buildRow(row) {
  const tr = document.createElement("tr");
  tr.tabIndex = 0;
  tr.dataset.symbol = row.symbol;
  tr.setAttribute("role", "button");
  tr.setAttribute(
    "aria-label",
    `${row.symbol}, ${row.name}, ${row.signal} signal, score ${row.score}, press Enter for detail`
  );

  const bt = row.backtest || {};
  const cells = [
    `<span class="symcell"><span class="ticker">${row.symbol}</span><span class="nick">${row.name}</span></span>`,
    fmtPrice(row.price),
    `<span class="${toneOf(row.change_1d)}">${fmtPct(row.change_1d)}</span>`,
    `<span class="${toneOf(row.change_30d)}">${fmtPct(row.change_30d)}</span>`,
    signalChip(row),
    scoreCell(row.score),
    row.entry != null ? fmtPrice(row.entry) : `<span class="dim">—</span>`,
    row.stop != null ? fmtPrice(row.stop) : `<span class="dim">—</span>`,
    row.target != null
      ? fmtPrice(row.target)
      : row.target_banked != null
      ? `<span class="pos" title="1.5R target already filled; remainder on a trail">✓ ${fmtPrice(
          row.target_banked
        )}</span>`
      : row.trail_atr != null
      ? `<span class="dim">trail ${nf(row.trail_atr, 0)}×ATR</span>`
      : `<span class="dim">—</span>`,
    bt.win_rate != null ? nf(bt.win_rate, 1) : `<span class="dim">—</span>`,
    `<span class="${toneOf(bt.expectancy_r)}">${bt.expectancy_r != null ? nf(bt.expectancy_r, 2) : "—"}</span>`,
    bt.trades != null ? bt.trades : `<span class="dim">—</span>`,
  ];

  tr.innerHTML = cells.map((html, i) => `<td${i === 0 ? ' class="col-sym"' : ""}>${html}</td>`).join("");
  tr.addEventListener("click", () => openDetail(row.symbol));
  tr.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openDetail(row.symbol);
    }
  });
  return tr;
}

function visibleRows() {
  const term = $("#search").value.trim().toUpperCase();
  let rows = state.rows.filter(
    (row) =>
      (state.filter === "all" || row.kind === state.filter) &&
      (!term || row.symbol.includes(term) || row.name.toUpperCase().includes(term))
  );
  const { key, dir } = state.sort;
  if (key) {
    rows = rows.slice().sort((a, b) => {
      const av = key === "win_rate" ? (a.backtest || {}).win_rate : a[key];
      const bv = key === "win_rate" ? (b.backtest || {}).win_rate : b[key];
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
  }
  return rows;
}

function renderTable() {
  const tbody = $("#rows");
  const rows = visibleRows();
  tbody.replaceChildren();

  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="12" class="empty">No symbols match this filter.</td>`;
    tbody.appendChild(tr);
    return;
  }

  const frag = document.createDocumentFragment();
  rows.forEach((row) => {
    const tr = buildRow(row);
    if (row.symbol === state.selected) tr.classList.add("is-open");
    frag.appendChild(tr);
  });
  tbody.appendChild(frag);

  const failed = Object.keys(state.errors);
  if (failed.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="12" class="errorbar">Could not load ${failed.join(", ")} — ${
      state.errors[failed[0]]
    }</td>`;
    tbody.appendChild(tr);
  }
}

function renderCounts() {
  const rows = state.rows.filter((row) => state.filter === "all" || row.kind === state.filter);
  const long = rows.filter((row) => row.signal === "LONG").length;
  const short = rows.filter((row) => row.signal === "SHORT").length;
  const flat = rows.length - long - short;
  $("#counts").innerHTML = `
    <span class="long"><span class="n">${long}</span> long</span>
    <span class="short"><span class="n">${short}</span> short</span>
    <span><span class="n">${flat}</span> flat</span>`;
}

/* ------------------------------------------------------------------ charts */

function axisLabel(v) {
  if (Math.abs(v) >= 1000) return nf(v, 0);
  if (Math.abs(v) >= 10) return nf(v, 1);
  if (Math.abs(v) >= 1) return nf(v, 2);
  return v.toFixed(4);
}

function chartPalette() {
  const styles = getComputedStyle(document.documentElement);
  const pick = (name, fallback) => (styles.getPropertyValue(name) || "").trim() || fallback;
  return {
    up: pick("--long", "#17b47f"),
    down: pick("--short", "#e8544f"),
    grid: pick("--chart-grid", "#151a21"),
    axis: pick("--chart-axis", "#55606d"),
    ema20: pick("--chart-ema20", "#5b8def"),
    ema50: pick("--chart-ema50", "#a78bfa"),
    ema200: pick("--chart-ema200", "#8b98a5"),
    last: pick("--accent", "#e5a83c"),
  };
}

function candleChart(detail) {
  const C = chartPalette();
  const W = 720;
  const H = 250;
  const padL = 6;
  const padR = 56;
  const padT = 10;
  const padB = 20;
  const candles = detail.candles || [];
  const n = candles.length;
  if (!n) return "";

  const all = [];
  candles.forEach((c) => {
    all.push(c.h, c.l);
  });
  [detail.ema20, detail.ema50, detail.ema200].forEach((series) =>
    (series || []).forEach((v) => {
      if (v != null) all.push(v);
    })
  );

  let min = Math.min(...all);
  let max = Math.max(...all);
  if (min === max) {
    min *= 0.99;
    max *= 1.01;
  }
  const breathing = (max - min) * 0.06;
  min -= breathing;
  max += breathing;

  const x = (i) => padL + (i / Math.max(n - 1, 1)) * (W - padL - padR);
  const y = (p) => padT + ((max - p) / (max - min)) * (H - padT - padB);
  const bodyWidth = Math.max(1, ((W - padL - padR) / n) * 0.6);

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price chart for ${detail.symbol}">`;

  for (let g = 0; g <= 4; g += 1) {
    const price = min + ((max - min) * g) / 4;
    const yy = y(price);
    svg += `<line x1="${padL}" x2="${W - padR}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(
      1
    )}" stroke="${C.grid}" stroke-width="1"/>`;
    svg += `<text x="${W - padR + 6}" y="${(yy + 3).toFixed(1)}" fill="${C.axis}" font-family="ui-monospace,monospace" font-size="9">${axisLabel(
      price
    )}</text>`;
  }

  candles.forEach((c, i) => {
    const up = c.c >= c.o;
    const color = up ? C.up : C.down;
    const xc = x(i);
    svg += `<line x1="${xc.toFixed(1)}" x2="${xc.toFixed(1)}" y1="${y(c.h).toFixed(1)}" y2="${y(
      c.l
    ).toFixed(1)}" stroke="${color}" stroke-width="1" opacity="0.65"/>`;
    const yo = y(c.o);
    const yc = y(c.c);
    const top = Math.min(yo, yc);
    const height = Math.max(1, Math.abs(yc - yo));
    svg += `<rect x="${(xc - bodyWidth / 2).toFixed(1)}" y="${top.toFixed(1)}" width="${bodyWidth.toFixed(
      1
    )}" height="${height.toFixed(1)}" fill="${color}" opacity="0.9"/>`;
  });

  [
    [detail.ema20, C.ema20],
    [detail.ema50, C.ema50],
    [detail.ema200, C.ema200],
  ].forEach(([series, color]) => {
    if (!series) return;
    let path = "";
    let started = false;
    series.forEach((v, i) => {
      if (v == null) return;
      path += `${started ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)} `;
      started = true;
    });
    if (path) svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="1" opacity="0.55"/>`;
  });

  (detail.marks || []).forEach((mark) => {
    const i = candles.findIndex((c) => c.t === mark.t);
    if (i < 0) return;
    const xc = x(i);
    if (mark.type === "entry" && mark.side > 0) {
      svg += `<path d="M${xc.toFixed(1)} ${(y(candles[i].l) + 10).toFixed(1)} l-4 7 l8 0 z" fill="${C.up}"/>`;
    } else if (mark.type === "entry" && mark.side < 0) {
      svg += `<path d="M${xc.toFixed(1)} ${(y(candles[i].h) - 10).toFixed(1)} l-4 -7 l8 0 z" fill="${C.down}"/>`;
    } else if (mark.type === "exit") {
      const yy = y(candles[i].c);
      svg += `<path d="M${(xc - 3).toFixed(1)} ${(yy - 6).toFixed(1)} l6 6 m0 -6 l-6 6" stroke="${C.axis}" stroke-width="1.1"/>`;
    }
  });

  const last = candles[n - 1];
  const lastY = y(last.c);
  svg += `<line x1="${padL}" x2="${W - padR}" y1="${lastY.toFixed(1)}" y2="${lastY.toFixed(
    1
  )}" stroke="${C.last}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>`;
  svg += `<text x="${W - padR + 6}" y="${(lastY + 3).toFixed(1)}" fill="${C.last}" font-family="ui-monospace,monospace" font-size="9">${axisLabel(
    last.c
  )}</text>`;

  const ticks = 4;
  for (let t = 0; t <= ticks; t += 1) {
    const i = Math.round(((n - 1) * t) / ticks);
    const anchor = t === 0 ? "start" : t === ticks ? "end" : "middle";
    svg += `<text x="${x(i).toFixed(1)}" y="${H - 6}" fill="${C.axis}" font-family="ui-monospace,monospace" font-size="9" text-anchor="${anchor}">${fmtDate(
      candles[i].t
    )}</text>`;
  }

  return `${svg}</svg>`;
}

function equityChart(curve) {
  if (!curve || curve.length < 2) return "";
  const C = chartPalette();
  const W = 720;
  const H = 110;
  const padT = 8;
  const padB = 14;
  const sample = [];
  const stride = Math.max(1, Math.floor(curve.length / 240));
  for (let i = 0; i < curve.length; i += stride) sample.push(curve[i]);
  if (sample[sample.length - 1] !== curve[curve.length - 1]) sample.push(curve[curve.length - 1]);

  const values = sample.map((point) => point.equity);
  const min = Math.min(...values, 1);
  const max = Math.max(...values, 1);
  const span = max - min || 0.01;
  const x = (i) => 6 + (i / Math.max(sample.length - 1, 1)) * (W - 12);
  const y = (v) => padT + ((max - v) / span) * (H - padT - padB);

  let path = `M${x(0).toFixed(1)} ${y(values[0]).toFixed(1)}`;
  sample.forEach((point, i) => {
    path += `L${x(i).toFixed(1)} ${y(point.equity).toFixed(1)}`;
  });
  const area = `${path}L${x(sample.length - 1).toFixed(1)} ${y(min).toFixed(1)}L${x(0).toFixed(1)} ${y(
    min
  ).toFixed(1)}Z`;
  const lastEquity = values[values.length - 1];
  const tone = lastEquity >= 1 ? C.up : C.down;
  const zero = y(1);

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Strategy equity curve">
    <line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(
    1
  )}" stroke="${C.grid}" stroke-width="1" stroke-dasharray="2 3"/>
    <path d="${area}" fill="${tone}" opacity="0.08"/>
    <path d="${path}" fill="none" stroke="${tone}" stroke-width="1.2"/>
    <text x="6" y="${H - 3}" fill="${C.axis}" font-family="ui-monospace,monospace" font-size="9">${fmtDate(
      sample[0].t
    )}</text>
    <text x="${W - 6}" y="${H - 3}" fill="${C.axis}" font-family="ui-monospace,monospace" font-size="9" text-anchor="end">${fmtDate(
      sample[sample.length - 1].t
    )}</text>
  </svg>`;
}

/* ------------------------------------------------------------------ drawer */

function statBlock(label, value, hint, tone) {
  return `<div class="stat"><div class="k">${label}</div><div class="v ${
    tone || ""
  }">${value}</div>${hint ? `<div class="h">${hint}</div>` : ""}</div>`;
}

function votesBlock(summary) {
  const votes = summary.votes || [];
  if (!votes.length) return `<div class="empty">No votes — insufficient history.</div>`;
  return `<div class="votes">${votes
    .map((vote) => {
      const label = vote.vote > 0 ? "LONG" : vote.vote < 0 ? "SHORT" : "NEUTRAL";
      const tone = vote.vote > 0 ? "pos" : vote.vote < 0 ? "neg" : "dim";
      return `<div class="vote">
        <span class="vl">${vote.label}</span>
        <span class="vv ${tone}">${vote.vote > 0 ? "+1" : vote.vote < 0 ? "−1" : "0"} ${label}</span>
        <span class="vd">${vote.detail}</span>
      </div>`;
    })
    .join("")}</div>`;
}

function tradesTable(trades) {
  if (!trades || !trades.length) return `<div class="empty">No closed trades in this window.</div>`;
  const rows = trades
    .slice()
    .reverse()
    .slice(0, 10)
    .map(
      (trade) => `<tr>
        <td>${trade.entry_date} → ${trade.exit_date}</td>
        <td class="${trade.side === "LONG" ? "pos" : "neg"}">${trade.side}</td>
        <td>${fmtPrice(trade.entry)}</td>
        <td>${fmtPrice(trade.exit)}</td>
        <td class="${toneOf(trade.ret_pct)}">${fmtPct(trade.ret_pct, 2)}</td>
        <td class="${toneOf(trade.r_multiple)}">${nf(trade.r_multiple, 2)}R</td>
        <td class="dim">${trade.reason}</td>
      </tr>`
    )
    .join("");
  return `<table class="trades">
    <thead><tr><th>Dates</th><th>Side</th><th>In</th><th>Out</th><th>Return</th><th>R</th><th>Exit</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderDrawer(detail) {
  const s = detail.summary || {};
  const bt = detail.backtest || {};
  const isLong = s.signal === "LONG";
  const isShort = s.signal === "SHORT";
  const tone = isLong ? "pos" : isShort ? "neg" : "dim";
  // Built as plain strings: nesting template literals inside a ternary inside
  // the drawer template is legal but fragile, and it broke the parser once.
  const exitStory =
    bt.exit_mode === "fixed"
      ? "a hard " + nf(3, 0) + "×ATR target (1.5R) closes the whole position"
      : bt.exit_mode === "partial"
      ? "half comes off at the " +
        nf(3, 0) +
        "×ATR target (1.5R) and the remainder trails " +
        nf(3, 0) +
        "×ATR behind the best price"
      : "the stop trails " + nf(3, 0) + "×ATR behind the best price, with no fixed target";
  const positionLine = isLong || isShort
    ? `Held ${s.bars_held} bars${s.entry_date ? ` since ${s.entry_date}` : ""} from ${fmtPrice(
        s.entry
      )} · ${fmtPct(s.unrealized_pct, 2)} · ${nf(s.unrealized_r, 2)}R`
    : s.exit_note
    ? s.exit_note
    : s.blocked
    ? `No position — ${s.block_reason}`
    : `No position. Bias ${String(s.bias || "neutral").replace("_", " ")} at score ${nf(s.score, 2)}.`;

  const locked =
    s.entry != null && s.stop != null && (s.stop - s.entry) * (isLong ? 1 : -1) > 0;
  const targetHint =
    s.target != null
      ? s.partial != null
        ? `${Math.round(s.partial * 100)}% out here`
        : "fixed exit"
      : s.target_banked != null
      ? `banked ${fmtPrice(s.target_banked)}`
      : s.partial_done
      ? "taken · trailing now"
      : "no cap";
  const levels = [
    ["Entry", s.entry != null ? fmtPrice(s.entry) : s.price != null ? `${fmtPrice(s.price)} (next open)` : "—", s.entry_date || ""],
    [
      "Stop",
      s.stop != null ? fmtPrice(s.stop) : "—",
      locked ? "trailed past entry" : s.trail_atr != null ? `initial ${nf(2, 0)}×ATR` : "2×ATR",
    ],
    [
      "Target",
      s.target != null
        ? fmtPrice(s.target)
        : s.trail_atr != null
        ? `trail ${nf(s.trail_atr, 0)}×ATR`
        : "—",
      targetHint,    ],
    [
      "Risk",
      s.risk_pct != null ? `${nf(s.risk_pct, 2)}%${s.rr != null ? ` · ${nf(s.rr, 1)}R` : ""}` : "—",
      "to stop, from here",
    ],
  ];

  const head = `<div class="dhead">
    <div>
      <h2 id="drawerTitle">${detail.symbol}</h2>
      <div class="sub">${detail.name} · ${detail.kind} · ${
    detail.universe_window
      ? `${detail.universe_window.bars} bars, ${detail.universe_window.start} → ${detail.universe_window.end}`
      : ""
  }</div>
    </div>
    <div class="price">
      <div class="p">${fmtPrice(s.price)}</div>
      <div class="c ${tone}"><span class="chip ${isLong ? "long" : isShort ? "short" : "flat"}">${
    s.signal || "—"
  }</span></div>
    </div>
  </div>`;

  const body = `<div class="dbody">
    <div class="block-title">Signal</div>
    <div class="statgrid">
      ${statBlock("Score", nf(s.score, 2), `${s.conviction || "—"} conviction`, tone)}
      ${statBlock("ADX", s.adx != null ? nf(s.adx, 1) : "—", "trend strength")}
      ${statBlock("RSI", s.rsi != null ? nf(s.rsi, 1) : "—", "14-day")}
      ${statBlock("ATR", s.atr_pct != null ? `${nf(s.atr_pct, 2)}%` : "—", "daily range")}
    </div>
    <div class="block-title">${isLong || isShort ? "Live position" : "Levels if triggered"}</div>
    <div class="statgrid">
      ${levels
        .map(([label, value, hint]) => statBlock(label, value, hint || ""))
        .join("")}
    </div>
    <p class="note" style="margin:10px 0 0;padding:0;border:0">${positionLine}</p>

    <div class="block-title">Why this reading</div>
    ${votesBlock(s)}

    <div class="block-title">Price · ${(detail.candles || []).length} sessions</div>
    <div class="chart-card">
      ${candleChart(detail)}
      <div class="legend">
        <span style="color:var(--chart-ema20)">EMA 20</span>
        <span style="color:var(--chart-ema50)">EMA 50</span>
        <span style="color:var(--chart-ema200)">EMA 200</span>
        <span style="color:var(--long)">▲ long entry</span>
        <span style="color:var(--short)">▼ short entry</span>
      </div>
    </div>

    <div class="block-title">Backtest · same rules, ${nf(bt.cost_bps_per_side, 0)} bps per side</div>
    <div class="statgrid five">
      ${statBlock("Trades", bt.trades ?? "—", (bt.exposure_pct ?? "—") + "% time in market")}
      ${statBlock("Win rate", bt.win_rate != null ? `${nf(bt.win_rate, 1)}%` : "—", `${bt.long_trades ?? 0}L / ${bt.short_trades ?? 0}S`)}
      ${statBlock("Target hit", bt.target_hit_rate != null ? `${nf(bt.target_hit_rate, 1)}%` : "—", "reached 1.5R")}
      ${statBlock("Expectancy", bt.expectancy_r != null ? `${nf(bt.expectancy_r, 2)}R` : "—", "per trade", toneOf(bt.expectancy_r))}
      ${statBlock("Profit factor", bt.profit_factor != null ? nf(bt.profit_factor, 2) : "—", "gross win / loss")}
      ${statBlock("Total", bt.total_return_pct != null ? fmtPct(bt.total_return_pct) : "—", "net of costs", toneOf(bt.total_return_pct))}
      ${statBlock("Buy & hold", bt.buy_hold_pct != null ? fmtPct(bt.buy_hold_pct) : "—", "same window", toneOf(bt.buy_hold_pct))}
      ${statBlock("Max DD", bt.max_drawdown_pct != null ? `${nf(bt.max_drawdown_pct, 1)}%` : "—", "peak to trough", "neg")}
      ${statBlock("Sharpe", bt.sharpe != null ? nf(bt.sharpe, 2) : "—", "daily, annualised", toneOf(bt.sharpe))}
      ${statBlock("t-stat", bt.t_stat != null ? nf(bt.t_stat, 2) : "—", "≥ 2 is suggestive")}
    </div>

    <div class="block-title">Equity</div>
    <div class="chart-card">${equityChart(bt.equity_curve)}
      <div class="legend"><span style="color:var(--muted)">strategy equity, marked daily · 1.0 at start</span></div>
    </div>

    <div class="block-title">Recent trades</div>
    ${tradesTable(detail.trade_log)}

    <p class="note">
      Signals evaluate after the close and are only actionable at the next open; the backtest applies that
      same lag. Stops start ${nf(2, 0)}×ATR from entry, then ${exitStory}, checked against each bar's
      high/low with gaps filled at the open. Results are in-sample on the window shown — they describe how
      these rules behaved on that history, not how they will behave next.
    </p>
  </div>`;

  $("#drawerBody").innerHTML = head + body;
}

/* --------------------------------------------------------------- lifecycle */

async function load(refresh = false) {
  if (state.loading) return;
  state.loading = true;
  const button = $("#refresh");
  button.disabled = true;
  $("#stamp").textContent = refresh ? "recomputing…" : "loading…";

  try {
    const data = await getJSON(`/api/signals?asset_class=all${refresh ? "&refresh=true" : ""}`);
    state.rows = data.rows || [];
    state.errors = data.errors || {};
    renderCounts();
    renderTable();

    const snapshot = readSnapshot();
    if (!snapshot) {
      writeSnapshot(state.rows); // first visit: remember the baseline silently
    } else {
      renderBanner(diffSignals(snapshot, state.rows), snapshot.at);
    }
    loadAlerts();
    loadTrades();
    const when = new Date(data.generated_at);
    $("#stamp").textContent = `${data.counts.long}L · ${data.counts.short}S · updated ${when.toLocaleTimeString(
      "en-GB",
      { hour: "2-digit", minute: "2-digit" }
    )}`;
  } catch (error) {
    $("#stamp").textContent = "feed unavailable";
    $("#rows").innerHTML = `<tr><td colspan="12" class="errorbar">Could not reach the signal service: ${error.message}</td></tr>`;
  } finally {
    state.loading = false;
    button.disabled = false;
  }
}

async function openDetail(symbol) {
  state.selected = symbol;
  $$("#rows tr").forEach((tr) => tr.classList.toggle("is-open", tr.dataset.symbol === symbol));
  const drawer = $("#drawer");
  const scrim = $("#scrim");
  drawer.hidden = false;
  scrim.hidden = false;
  $("#drawerBody").innerHTML = `<div class="dbody"><div class="sk" style="width:60%;height:14px"></div>
    <div class="sk" style="width:90%;margin-top:16px"></div></div>`;
  $("#close").focus();

  try {
    const detail = await getJSON(`/api/asset/${encodeURIComponent(symbol)}?bars=220`);
    state.detail = detail;
    renderDrawer(detail);
  } catch (error) {
    $("#drawerBody").innerHTML = `<div class="dbody"><div class="errorbar">Could not load ${symbol}: ${error.message}</div></div>`;
  }
}

function closeDetail() {
  $("#drawer").hidden = true;
  $("#scrim").hidden = true;
  const previous = state.selected;
  state.selected = null;
  $$("#rows tr").forEach((tr) => tr.classList.remove("is-open"));
  const row = previous && $(`#rows tr[data-symbol="${previous}"]`);
  if (row) row.focus();
}

function bind() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((other) => {
        other.classList.remove("is-active");
        other.setAttribute("aria-selected", "false");
      });
      tab.classList.add("is-active");
      tab.setAttribute("aria-selected", "true");
      state.filter = tab.dataset.class;
      renderCounts();
      renderActiveView();
    });
  });

  $("#search").addEventListener("input", renderActiveView);

  $$("th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      const dir = state.sort.key === key ? -state.sort.dir : -1;
      state.sort = { key, dir };
      $$("th.sortable").forEach((other) => {
        const arrow = other.querySelector(".arrow");
        if (arrow) arrow.remove();
      });
      th.insertAdjacentHTML("beforeend", `<span class="arrow">${dir < 0 ? "↓" : "↑"}</span>`);
      renderTable();
    });
  });

  $("#refresh").addEventListener("click", () => load(true));
  $("#theme").addEventListener("click", toggleTheme);
  $$(".view").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("#alertWindow").addEventListener("change", (event) => {
    state.alertDays = Number(event.target.value) || 30;
    loadAlerts();
  });
  $("#alertRows").addEventListener("click", (event) => {
    const button = event.target.closest("[data-log]");
    if (!button) return;
    const row = state.alerts.find((item) => `${item.symbol}:${item.date}:${item.to}` === button.dataset.log);
    if (row) logTrade(row, button);
  });
  $("#tradeRows").addEventListener("click", (event) => {
    const symbol = event.target.closest("[data-open-symbol]");
    if (symbol) {
      openDetail(symbol.dataset.openSymbol);
      return;
    }
    const close = event.target.closest("[data-close]");
    if (close) {
      closeTrade(Number(close.dataset.close));
      return;
    }
    const remove = event.target.closest("[data-delete]");
    if (remove) deleteTrade(Number(remove.dataset.delete));
  });
  $("#banner").addEventListener("click", (event) => {
    const chip = event.target.closest(".chg");
    if (chip) {
      openDetail(chip.dataset.symbol);
      return;
    }
    if (event.target.closest("#bannerDismiss")) acknowledgeChanges();
  });
  $("#close").addEventListener("click", closeDetail);
  $("#scrim").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("#drawer").hidden) closeDetail();
  });
}

initTheme();
bind();
if (location.hash === "#alerts" || location.hash === "#trades") {
  switchView(location.hash.slice(1), false);
}
load();
