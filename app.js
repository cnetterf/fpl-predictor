const state = {
  activeView: "predictor",
  predictor: {
    dataset: null,
    availableGameweeks: [],
    activeSource: "official",
    selectedTeams: new Set(),
    teamsInitialized: false,
    sortKey: "total",
    sortDirection: "desc",
  },
  backtest: {
    dataset: null,
    availableGameweeks: [],
    allTeams: [],
    selectedTeams: new Set(),
    teamsInitialized: false,
    playerQuery: "",
    positionFilter: "ALL",
    detailSource: "all",
    groupBy: "player",
    sortKey: "absolute_error",
    sortDirection: "desc",
    localAvailable: false,
    windowOverrides: {},
    windowDetails: {},
    isLoading: false,
    hasLoaded: false,
    horizon: 4,
    activeDetailStartGw: null,
  },
};

const elements = {
  viewButtons: document.querySelectorAll("[data-view]"),
  views: {
    predictor: document.getElementById("predictorView"),
    backtest: document.getElementById("backtestView"),
  },

  startGw: document.getElementById("startGw"),
  endGw: document.getElementById("endGw"),
  rangeValue: document.getElementById("rangeValue"),
  rangeSpan: document.getElementById("rangeSpan"),
  rangeFill: document.getElementById("rangeFill"),
  horizonLabels: document.getElementById("horizonLabels"),
  positionFilter: document.getElementById("positionFilter"),
  teamFilterList: document.getElementById("teamFilterList"),
  selectAllTeamsButton: document.getElementById("selectAllTeamsButton"),
  clearAllTeamsButton: document.getElementById("clearAllTeamsButton"),
  showBonus: document.getElementById("showBonus"),
  showYellows: document.getElementById("showYellows"),
  refreshButton: document.getElementById("refreshButton"),
  sourceButtons: document.querySelectorAll("[data-source]"),
  playerCount: document.getElementById("playerCount"),
  statusText: document.getElementById("statusText"),
  resultsBody: document.getElementById("resultsBody"),
  optionalHeaders: document.querySelectorAll("[data-optional]"),
  sortButtons: document.querySelectorAll("[data-sort]"),

  backtestStartGw: document.getElementById("backtestStartGw"),
  backtestEndGw: document.getElementById("backtestEndGw"),
  backtestRangeValue: document.getElementById("backtestRangeValue"),
  backtestRangeSpan: document.getElementById("backtestRangeSpan"),
  backtestRangeFill: document.getElementById("backtestRangeFill"),
  backtestRangeLabels: document.getElementById("backtestRangeLabels"),
  backtestHorizonInput: document.getElementById("backtestHorizonInput"),
  backtestPositionFilter: document.getElementById("backtestPositionFilter"),
  backtestGroupBy: document.getElementById("backtestGroupBy"),
  backtestPlayerSearch: document.getElementById("backtestPlayerSearch"),
  backtestTeamFilterList: document.getElementById("backtestTeamFilterList"),
  backtestSelectAllTeamsButton: document.getElementById("backtestSelectAllTeamsButton"),
  backtestClearAllTeamsButton: document.getElementById("backtestClearAllTeamsButton"),
  backtestSourceButtons: document.querySelectorAll("[data-backtest-source]"),
  backtestSortButtons: document.querySelectorAll("[data-backtest-sort]"),
  backtestSummaryCards: document.getElementById("backtestSummaryCards"),
  backtestBreakdownBody: document.getElementById("backtestBreakdownBody"),
  backtestRowsBody: document.getElementById("backtestRowsBody"),
  backtestTrendChart: document.getElementById("backtestTrendChart"),
  backtestSpanChart: document.getElementById("backtestSpanChart"),
  backtestStatusText: document.getElementById("backtestStatusText"),
  backtestDetailWindowStatus: document.getElementById("backtestDetailWindowStatus"),
  backtestDetailComponentsBody: document.getElementById("backtestDetailComponentsBody"),
  backtestAggregateStatus: document.getElementById("backtestAggregateStatus"),
  backtestAggregateComponentsBody: document.getElementById("backtestAggregateComponentsBody"),
  backtestModeText: document.getElementById("backtestModeText"),
  backtestLocalStatus: document.getElementById("backtestLocalStatus"),
  backtestRecomputeButton: document.getElementById("backtestRecomputeButton"),
  backtestTrendNote: document.getElementById("backtestTrendNote"),
  backtestSpanNote: document.getElementById("backtestSpanNote"),

  playerModal: document.getElementById("playerModal"),
  modalTitle: document.getElementById("modalTitle"),
  modalSubtitle: document.getElementById("modalSubtitle"),
  modalContent: document.getElementById("modalContent"),
  closeModalButton: document.getElementById("closeModalButton"),
};

function formatNumber(value, digits = 2) {
  return Number(value || 0).toFixed(digits);
}

function formatSigned(value, digits = 2) {
  const number = Number(value || 0);
  if (number > 0) {
    return `+${number.toFixed(digits)}`;
  }
  return number.toFixed(digits);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function mean(values) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function switchView(viewKey) {
  state.activeView = viewKey;
  Object.entries(elements.views).forEach(([key, view]) => {
    view.hidden = key !== viewKey;
  });
  elements.viewButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === viewKey);
  });
  if (viewKey === "backtest") {
    ensureBacktestViewLoaded();
  }
}

function updateViewUrl(viewKey) {
  const url = new URL(window.location.href);
  url.searchParams.set("view", viewKey);
  window.history.replaceState({}, "", url);
}

function detailRows(rows) {
  return rows.map(([label, value]) => (
    `<div class="detail-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
  )).join("");
}

function matchesMarkup(matches) {
  if (!matches || matches.length === 0) {
    return "<p class=\"modal-subtitle\">No recent matches in sample.</p>";
  }
  return `<ol class="detail-matches">${matches.map((match) => (
    `<li>GW${escapeHtml(match.round)}: ${escapeHtml(match.minutes)} mins, starts ${escapeHtml(match.starts)}</li>`
  )).join("")}</ol>`;
}

function fixtureLabel(fixture) {
  return `GW${fixture.event}: ${fixture.opponent} (${fixture.home ? "H" : "A"}, diff ${fixture.difficulty})`;
}

function sourceDetailMarkup(label, player) {
  const inputs = player.inputs || {};
  const goalModel = inputs.goal_model || {};
  const assistModel = inputs.assist_model || {};

  return `
    <section class="stack">
      <div class="source-kicker">${escapeHtml(label)}</div>
      <article class="detail-card">
        <h3>Total</h3>
        <div class="metric-list">
          ${detailRows([
            ["Displayed total", formatNumber(displayedTotalPoints(player))],
            ["Model total", formatNumber(player.predicted_total_points)],
            ["Minutes points", formatNumber(player.components.minutes_points)],
            ["Goal points", formatNumber(player.components.goal_points)],
            ["Assist points", formatNumber(player.components.assist_points)],
            ["Clean sheet points", formatNumber(player.components.clean_sheet_points)],
            ["Defensive points", formatNumber(player.components.defensive_contribution_points)],
            ["Bonus points", formatNumber(player.components.bonus_points)],
            ["Yellow-card deduction", formatNumber(player.components.yellow_cards)],
          ])}
        </div>
      </article>
      <article class="detail-card">
        <h3>Minutes</h3>
        <div class="metric-list">
          ${detailRows([
            ["Predicted minutes / fixture", formatNumber(inputs.predicted_minutes_per_fixture || 0)],
            ["Minutes points / fixture", formatNumber(inputs.minutes_points_per_fixture || 0)],
            ["Base recent-minutes average", formatNumber(inputs.minutes_base || 0)],
            ["Availability factor", formatNumber(inputs.availability_factor || 0, 3)],
            ["Rotation factor", formatNumber(inputs.rotation_factor || 0, 3)],
          ])}
        </div>
        ${matchesMarkup(inputs.minutes_sample)}
      </article>
      <article class="detail-card">
        <h3>Goals</h3>
        <div class="metric-list">
          ${detailRows([
            ["Predicted goals / fixture", formatNumber(inputs.goals_per_fixture || 0, 3)],
            ["Recent xG total", formatNumber(goalModel.recent_xg_total || 0, 3)],
            ["Recent goals total", formatNumber(goalModel.recent_goals_total || 0, 3)],
            ["Baseline / fixture", formatNumber(goalModel.baseline_per_fixture || 0, 3)],
            ["Finishing adjustment", formatNumber(goalModel.finishing_adjustment || 0, 3)],
            ["Fixture factor", formatNumber(goalModel.fixture_factor || 0, 3)],
          ])}
        </div>
      </article>
      <article class="detail-card">
        <h3>Assists And Extras</h3>
        <div class="metric-list">
          ${detailRows([
            ["Predicted assists / fixture", formatNumber(inputs.assists_per_fixture || 0, 3)],
            ["Recent xA total", formatNumber(assistModel.recent_xa_total || 0, 3)],
            ["Recent assists total", formatNumber(assistModel.recent_assists_total || 0, 3)],
            ["CS probability / fixture", formatNumber(inputs.clean_sheet_probability_per_fixture || 0, 3)],
            ["Bonus / fixture", formatNumber(inputs.bonus_per_fixture || 0, 3)],
            ["Yellow cards / fixture", formatNumber(inputs.yellow_cards_per_fixture || 0, 3)],
          ])}
        </div>
      </article>
    </section>
  `;
}

function closeModal() {
  elements.playerModal.hidden = true;
}

function displayedTotalPoints(player) {
  let total = Number(player.predicted_total_points);
  if (!elements.showBonus.checked) {
    total -= Number(player.components.bonus_points || 0);
  }
  if (!elements.showYellows.checked) {
    total += Number(player.components.yellow_cards || 0);
  }
  return total;
}

function getPredictorSourceData(sourceKey = state.predictor.activeSource) {
  return state.predictor.dataset?.sources?.[sourceKey] || null;
}

function getPredictorStartIndex() {
  return Number(elements.startGw.value);
}

function getPredictorEndIndex() {
  return Number(elements.endGw.value);
}

function getPredictorSelectedGameweeks() {
  const gameweeks = state.predictor.availableGameweeks;
  const startIndex = getPredictorStartIndex();
  const endIndex = getPredictorEndIndex();
  return {
    start: gameweeks[startIndex] ?? null,
    end: gameweeks[endIndex] ?? null,
    span: endIndex - startIndex + 1,
  };
}

function getPredictorAllTeams() {
  if (!state.predictor.dataset) {
    return [];
  }
  const teams = new Set();
  Object.values(state.predictor.dataset.sources || {}).forEach((source) => {
    Object.values(source.predictions || {}).forEach((endMap) => {
      Object.values(endMap || {}).forEach((players) => {
        players.forEach((player) => {
          if (player.team) {
            teams.add(player.team);
          }
        });
      });
    });
  });
  return [...teams].sort();
}

function ensurePredictorSelectedTeams() {
  if (state.predictor.teamsInitialized) {
    return;
  }
  getPredictorAllTeams().forEach((team) => state.predictor.selectedTeams.add(team));
  state.predictor.teamsInitialized = true;
}

function renderPredictorRangeLabels() {
  const labels = state.predictor.availableGameweeks.map((gameweek, index) => {
    const selected = index >= getPredictorStartIndex() && index <= getPredictorEndIndex();
    return `<span style="font-weight:${selected ? "700" : "400"}">GW${gameweek}</span>`;
  });
  elements.horizonLabels.innerHTML = labels.join("");
}

function renderPredictorRangeFill() {
  const maxIndex = Math.max(state.predictor.availableGameweeks.length - 1, 1);
  const startPercent = (getPredictorStartIndex() / maxIndex) * 100;
  const endPercent = (getPredictorEndIndex() / maxIndex) * 100;
  elements.rangeFill.style.left = `${startPercent}%`;
  elements.rangeFill.style.width = `${Math.max(endPercent - startPercent, 0)}%`;
}

function updatePredictorRangeSummary() {
  const selected = getPredictorSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    elements.rangeValue.textContent = "No gameweeks available";
    elements.rangeSpan.textContent = "";
    return;
  }
  elements.rangeValue.textContent = `GW ${selected.start} to GW ${selected.end}`;
  elements.rangeSpan.textContent = `${selected.span} week${selected.span === 1 ? "" : "s"}`;
}

function applyPredictorStartBounds() {
  const maxIndex = state.predictor.availableGameweeks.length - 1;
  const startIndex = getPredictorStartIndex();
  let endIndex = getPredictorEndIndex();
  if (startIndex > endIndex) {
    endIndex = startIndex;
  }
  if (endIndex - startIndex >= 6) {
    endIndex = Math.min(startIndex + 5, maxIndex);
  }
  elements.endGw.value = String(endIndex);
}

function applyPredictorEndBounds() {
  let startIndex = getPredictorStartIndex();
  const endIndex = getPredictorEndIndex();
  if (endIndex < startIndex) {
    startIndex = endIndex;
  }
  if (endIndex - startIndex >= 6) {
    startIndex = endIndex - 5;
  }
  elements.startGw.value = String(startIndex);
}

function configurePredictorRangeControl() {
  const gameweeks = state.predictor.dataset?.available_gameweeks || [];
  state.predictor.availableGameweeks = gameweeks;
  const maxIndex = Math.max(gameweeks.length - 1, 0);
  elements.startGw.min = "0";
  elements.startGw.max = String(maxIndex);
  elements.endGw.min = "0";
  elements.endGw.max = String(maxIndex);
  elements.startGw.value = "0";
  elements.endGw.value = String(Math.min(maxIndex, 5));
  renderPredictorRangeLabels();
  renderPredictorRangeFill();
  updatePredictorRangeSummary();
}

function updatePredictorSourceButtons() {
  elements.sourceButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.source === state.predictor.activeSource);
  });
}

function renderPredictorTeamFilter() {
  const teams = getPredictorAllTeams();
  ensurePredictorSelectedTeams();
  elements.teamFilterList.innerHTML = teams.map((team) => `
    <label class="team-option">
      <input type="checkbox" value="${escapeHtml(team)}" ${state.predictor.selectedTeams.has(team) ? "checked" : ""}>
      <span>${escapeHtml(team)}</span>
    </label>
  `).join("");
}

function getPredictorWindowPlayers(sourceKey = state.predictor.activeSource) {
  const sourceData = getPredictorSourceData(sourceKey);
  if (!sourceData || state.predictor.availableGameweeks.length === 0) {
    return [];
  }
  const selected = getPredictorSelectedGameweeks();
  const startBucket = (sourceData.predictions || {})[String(selected.start)] || {};
  const rows = startBucket[String(selected.end)] || [];
  return rows.filter((player) => {
    const positionMatch = elements.positionFilter.value === "ALL" || player.position === elements.positionFilter.value;
    const teamMatch = state.predictor.selectedTeams.has(player.team);
    return positionMatch && teamMatch;
  });
}

function predictorSortValue(player, sortKey) {
  const components = player.components;
  return {
    player: `${player.player_name} ${player.team} ${player.position}`,
    total: Number(displayedTotalPoints(player)),
    minutes: Number(player.inputs?.predicted_minutes_per_fixture || 0),
    goal: Number(components.goal_points),
    assist: Number(components.assist_points),
    clean_sheet: Number(components.clean_sheet_points),
    defensive: Number(components.defensive_contribution_points),
    bonus: Number(components.bonus_points),
    yellow: Number(components.yellow_cards),
    fixtures: player.fixtures.map(fixtureLabel).join(" | "),
  }[sortKey];
}

function comparePredictorPlayers(left, right) {
  const leftValue = predictorSortValue(left, state.predictor.sortKey);
  const rightValue = predictorSortValue(right, state.predictor.sortKey);
  const direction = state.predictor.sortDirection === "asc" ? 1 : -1;
  if (typeof leftValue === "string" || typeof rightValue === "string") {
    return leftValue.localeCompare(rightValue) * direction;
  }
  if (leftValue === rightValue) {
    return Number(right.predicted_total_points) - Number(left.predicted_total_points);
  }
  return (leftValue - rightValue) * direction;
}

function updateOptionalColumns() {
  const mapping = {
    bonus: elements.showBonus.checked,
    yellow: elements.showYellows.checked,
  };

  elements.optionalHeaders.forEach((header) => {
    header.style.display = mapping[header.dataset.optional] ? "" : "none";
  });

  document.querySelectorAll("[data-cell-optional]").forEach((cell) => {
    cell.style.display = mapping[cell.dataset.cellOptional] ? "" : "none";
  });
}

function updatePredictorSortButtons() {
  elements.sortButtons.forEach((button) => {
    button.dataset.direction = button.dataset.sort === state.predictor.sortKey ? state.predictor.sortDirection : "";
  });
}

function openPredictorPlayerModal(playerId) {
  const selected = getPredictorSelectedGameweeks();
  const compared = Object.entries(state.predictor.dataset?.sources || {})
    .map(([sourceKey, source]) => {
      const startBucket = (source.predictions || {})[String(selected.start)] || {};
      const player = (startBucket[String(selected.end)] || []).find((item) => String(item.player_id) === String(playerId));
      return { source, player };
    })
    .filter((entry) => entry.player);

  if (compared.length === 0) {
    return;
  }

  const primaryPlayer = compared[0].player;
  elements.modalTitle.textContent = primaryPlayer.player_name;
  elements.modalSubtitle.textContent = `${primaryPlayer.team} · ${primaryPlayer.position} · ${primaryPlayer.fixtures.map(fixtureLabel).join(" / ")}`;
  elements.modalContent.innerHTML = compared
    .map(({ source, player }) => sourceDetailMarkup(source.label, player))
    .join("");
  elements.playerModal.hidden = false;
}

function renderPredictorTable() {
  const players = [...getPredictorWindowPlayers()].sort(comparePredictorPlayers);
  if (players.length === 0) {
    elements.resultsBody.innerHTML = `<tr><td colspan="10">No prediction rows are available for this filter combination.</td></tr>`;
    elements.playerCount.textContent = "0";
    updateOptionalColumns();
    updatePredictorSortButtons();
    return;
  }

  elements.resultsBody.innerHTML = players.map((player, index) => `
    <tr class="${index < 5 ? "top-pick" : ""}">
      <td>
        <button class="player-button" type="button" data-player-id="${player.player_id}">
          <strong>${escapeHtml(player.player_name)}</strong>
        </button><br>
        ${escapeHtml(player.team)} · ${escapeHtml(player.position)}
      </td>
      <td><strong>${formatNumber(displayedTotalPoints(player))}</strong></td>
      <td>${formatNumber(player.components.minutes_points)}</td>
      <td>${formatNumber(player.components.goal_points)}</td>
      <td>${formatNumber(player.components.assist_points)}</td>
      <td>${formatNumber(player.components.clean_sheet_points)}</td>
      <td>${formatNumber(player.components.defensive_contribution_points)}</td>
      <td data-cell-optional="bonus">${formatNumber(player.components.bonus_points)}</td>
      <td data-cell-optional="yellow">-${formatNumber(player.components.yellow_cards)}</td>
      <td class="fixture-list">${player.fixtures.map(fixtureLabel).join("<br>")}</td>
    </tr>
  `).join("");

  elements.playerCount.textContent = String(players.length);
  updateOptionalColumns();
  updatePredictorSortButtons();
}

function updatePredictorStatus(prefix = "Showing") {
  const dataset = state.predictor.dataset;
  const selected = getPredictorSelectedGameweeks();
  const sourceLabel = getPredictorSourceData()?.label || state.predictor.activeSource;
  const generatedAt = dataset?.generated_at ? new Date(dataset.generated_at).toLocaleString() : "unknown time";
  const sourceFetchAt = dataset?.source_last_fetch_at ? new Date(dataset.source_last_fetch_at).toLocaleString() : "unknown source fetch time";
  const cacheNote = dataset?.used_cached_data ? " Using cached source data." : "";
  elements.statusText.textContent = `${prefix} ${sourceLabel} predictions from GW${selected.start} to GW${selected.end} from ${generatedAt}. Source fetch: ${sourceFetchAt}.${cacheNote}`;
}

async function loadPredictions() {
  const dataUrl = window.FPL_DATA_URL || "./data/static_predictions.json";
  elements.statusText.textContent = "Loading static prediction data...";
  try {
    const response = await fetch(dataUrl, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Static data request failed");
    }
    state.predictor.dataset = payload;
    state.predictor.activeSource = payload.default_source || "official";
    state.predictor.selectedTeams = new Set();
    state.predictor.teamsInitialized = false;
    configurePredictorRangeControl();
    updatePredictorSourceButtons();
    renderPredictorTeamFilter();
    renderPredictorTable();
    updatePredictorStatus("Static data updated");
  } catch (error) {
    elements.statusText.textContent = `Static data load failed: ${error.message}`;
    elements.resultsBody.innerHTML = "";
    elements.playerCount.textContent = "0";
  }
}

function refreshPredictorView() {
  if (!state.predictor.dataset) {
    return;
  }
  renderPredictorTeamFilter();
  renderPredictorRangeLabels();
  renderPredictorRangeFill();
  updatePredictorRangeSummary();
  renderPredictorTable();
  updatePredictorStatus("Showing");
}

function getBacktestStartIndex() {
  return Number(elements.backtestStartGw.value);
}

function getBacktestEndIndex() {
  return Number(elements.backtestEndGw.value);
}

function getBacktestSelectedGameweeks() {
  const gameweeks = state.backtest.availableGameweeks;
  const startIndex = getBacktestStartIndex();
  const endIndex = getBacktestEndIndex();
  return {
    start: gameweeks[startIndex] ?? null,
    end: gameweeks[endIndex] ?? null,
    span: endIndex - startIndex + 1,
  };
}

function backtestWindowKey(startGw, endGw) {
  return `${startGw}-${endGw}`;
}

function getCurrentBacktestWindowKey() {
  const selected = getBacktestSelectedGameweeks();
  return selected.start === null || selected.end === null ? null : backtestWindowKey(selected.start, selected.end);
}

function getValidBacktestHorizon() {
  const selected = getBacktestSelectedGameweeks();
  const maxHorizon = Math.max((selected.end ?? 0) - (selected.start ?? 0) + 1, 1);
  const normalized = Math.min(Math.max(Number(state.backtest.horizon) || 1, 1), maxHorizon);
  state.backtest.horizon = normalized;
  if (elements.backtestHorizonInput.value !== String(normalized)) {
    elements.backtestHorizonInput.value = String(normalized);
  }
  return normalized;
}

function getBacktestHorizonWindows() {
  const selected = getBacktestSelectedGameweeks();
  const horizon = getValidBacktestHorizon();
  if (selected.start === null || selected.end === null) {
    return [];
  }
  const windows = [];
  for (let startGw = selected.start; startGw <= selected.end - horizon + 1; startGw += 1) {
    const endGw = startGw + horizon - 1;
    const key = backtestWindowKey(startGw, endGw);
    const payload = getBacktestWindowPayload(key);
    if (payload) {
      windows.push({ key, start_gw: startGw, end_gw: endGw, payload });
    }
  }
  return windows;
}

function getBacktestRangeWindows() {
  const selected = getBacktestSelectedGameweeks();
  if (selected.start === null || selected.end === null || !state.backtest.dataset) {
    return [];
  }
  return Object.entries(state.backtest.dataset.windows || {})
    .map(([key, payload]) => ({ key, ...payload }))
    .filter((windowEntry) => windowEntry.start_gw >= selected.start && windowEntry.end_gw <= selected.end);
}

function ensureActiveDetailWindow() {
  const windows = getBacktestHorizonWindows();
  if (windows.length === 0) {
    state.backtest.activeDetailStartGw = null;
    return null;
  }
  const active = windows.find((windowEntry) => windowEntry.start_gw === state.backtest.activeDetailStartGw);
  if (active) {
    return active;
  }
  state.backtest.activeDetailStartGw = windows[0].start_gw;
  return windows[0];
}

function updateBacktestModeText() {
  const key = getCurrentBacktestWindowKey();
  elements.backtestModeText.textContent = key && state.backtest.windowOverrides[key] ? "Local recompute active for this window" : "Static snapshot";
}

function renderBacktestRangeLabels() {
  const labels = state.backtest.availableGameweeks.map((gameweek, index) => {
    const selected = index >= getBacktestStartIndex() && index <= getBacktestEndIndex();
    return `<span style="font-weight:${selected ? "700" : "400"}">GW${gameweek}</span>`;
  });
  elements.backtestRangeLabels.innerHTML = labels.join("");
}

function renderBacktestRangeFill() {
  const maxIndex = Math.max(state.backtest.availableGameweeks.length - 1, 1);
  const startPercent = (getBacktestStartIndex() / maxIndex) * 100;
  const endPercent = (getBacktestEndIndex() / maxIndex) * 100;
  elements.backtestRangeFill.style.left = `${startPercent}%`;
  elements.backtestRangeFill.style.width = `${Math.max(endPercent - startPercent, 0)}%`;
}

function updateBacktestRangeSummary() {
  const selected = getBacktestSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    elements.backtestRangeValue.textContent = "No finished gameweeks available";
    elements.backtestRangeSpan.textContent = "";
    return;
  }
  elements.backtestRangeValue.textContent = `GW ${selected.start} to GW ${selected.end}`;
  elements.backtestRangeSpan.textContent = `${selected.span} week${selected.span === 1 ? "" : "s"}`;
  updateBacktestModeText();
}

function applyBacktestStartBounds() {
  const startIndex = getBacktestStartIndex();
  let endIndex = getBacktestEndIndex();
  if (startIndex > endIndex) {
    endIndex = startIndex;
  }
  elements.backtestEndGw.value = String(endIndex);
}

function applyBacktestEndBounds() {
  let startIndex = getBacktestStartIndex();
  const endIndex = getBacktestEndIndex();
  if (endIndex < startIndex) {
    startIndex = endIndex;
  }
  elements.backtestStartGw.value = String(startIndex);
}

function configureBacktestRangeControl() {
  const gameweeks = state.backtest.dataset?.available_gameweeks || [];
  state.backtest.availableGameweeks = gameweeks;
  const maxIndex = Math.max(gameweeks.length - 1, 0);
  elements.backtestStartGw.min = "0";
  elements.backtestStartGw.max = String(maxIndex);
  elements.backtestEndGw.min = "0";
  elements.backtestEndGw.max = String(maxIndex);
  elements.backtestStartGw.value = "0";
  elements.backtestEndGw.value = String(maxIndex);
  if (!state.backtest.horizon) {
    state.backtest.horizon = Math.min(4, Math.max(gameweeks.length, 1));
  }
  elements.backtestHorizonInput.value = String(state.backtest.horizon);
  renderBacktestRangeLabels();
  renderBacktestRangeFill();
  updateBacktestRangeSummary();
}

function unpackBacktestRows(sourceKey, packedRows) {
  const label = state.backtest.dataset?.sources?.[sourceKey] || sourceKey;
  const lookup = state.backtest.dataset?.player_lookup || {};
  return (packedRows || []).map((row) => {
    const playerLookup = lookup[String(row[0])] || [`Player ${row[0]}`, "", ""];
    return {
      player_id: row[0],
      predicted_points: Number(row[1]),
      actual_points: Number(row[2]),
      error: Number(row[3]),
      absolute_error: Number(row[4]),
      team: playerLookup[1],
      position: playerLookup[2],
      predicted_rank: Number(row[5]),
      actual_rank: Number(row[6]),
      rank_error: Number(row[7]),
      predicted_components: {
        minutes_points: Number(row[8]),
        goal_points: Number(row[9]),
        assist_points: Number(row[10]),
        clean_sheet_points: Number(row[11]),
        defensive_contribution_points: Number(row[12]),
        bonus_points: Number(row[13]),
        yellow_deduction: Number(row[14]),
        other_points: Number(row[15]),
      },
      actual_components: {
        minutes_points: Number(row[16]),
        goal_points: Number(row[17]),
        assist_points: Number(row[18]),
        clean_sheet_points: Number(row[19]),
        defensive_contribution_points: Number(row[20]),
        bonus_points: Number(row[21]),
        yellow_deduction: Number(row[22]),
        other_points: Number(row[23]),
      },
      predicted_stats: {
        goals: Number(row[24]),
        assists: Number(row[25]),
        clean_sheets: Number(row[26]),
        bonus: Number(row[27]),
        yellow_cards: Number(row[28]),
        expected_goals: Number(row[29]),
        expected_assists: Number(row[30]),
        defensive_contribution: Number(row[31]),
      },
      actual_stats: {
        goals: Number(row[32]),
        assists: Number(row[33]),
        clean_sheets: Number(row[34]),
        bonus: Number(row[35]),
        yellow_cards: Number(row[36]),
        expected_goals: Number(row[37]),
        expected_assists: Number(row[38]),
        defensive_contribution: Number(row[39]),
      },
      source: sourceKey,
      source_label: label,
      player_name: playerLookup[0],
    };
  });
}

function getBacktestWindowPayload(key = getCurrentBacktestWindowKey()) {
  if (!key || !state.backtest.dataset) {
    return null;
  }
  const baseWindow = state.backtest.dataset.windows?.[key];
  if (!baseWindow) {
    return null;
  }
  const override = state.backtest.windowOverrides[key];
  if (!override) {
    return baseWindow;
  }
  return {
    ...baseWindow,
    audit: override.audit || baseWindow.audit,
    sources: {
      ...baseWindow.sources,
      ...(override.sources || {}),
    },
  };
}

function getActiveDetailWindowPayload() {
  const detailWindow = ensureActiveDetailWindow();
  if (!detailWindow) {
    return null;
  }
  const detailOverride = state.backtest.windowDetails[detailWindow.key];
  if (!detailOverride) {
    loadBacktestDetailWindow(detailWindow.key);
    return detailWindow.payload;
  }
  return {
    ...detailWindow.payload,
    audit: detailOverride.audit || detailWindow.payload.audit,
    sources: {
      ...detailWindow.payload.sources,
      ...detailOverride.sources,
    },
  };
}

async function loadBacktestDetailWindow(key) {
  if (state.backtest.windowDetails[key]) {
    return;
  }
  const summaryWindow = state.backtest.dataset?.windows?.[key];
  if (!summaryWindow) {
    return;
  }
  try {
    const dataUrl = window.FPL_BACKTEST_WINDOWS_BASE_URL || "./data/backtest_windows";
    const response = await fetch(`${dataUrl}/${key}.json`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Static backtest detail request failed");
    }
    state.backtest.windowDetails[key] = {
      audit: payload.audit || {},
      sources: payload.sources || {},
    };
    if (ensureActiveDetailWindow()?.key === key) {
      refreshBacktestView();
    }
  } catch (error) {
    elements.backtestDetailWindowStatus.textContent = `Failed to load detail window ${key}: ${error.message}`;
  }
}

function buildBacktestAllTeams() {
  const teams = new Set();
  Object.values(state.backtest.dataset?.player_lookup || {}).forEach((value) => {
    if (value[1]) {
      teams.add(value[1]);
    }
  });
  state.backtest.allTeams = [...teams].sort();
}

function ensureBacktestSelectedTeams() {
  if (state.backtest.teamsInitialized) {
    return;
  }
  state.backtest.allTeams.forEach((team) => state.backtest.selectedTeams.add(team));
  state.backtest.teamsInitialized = true;
}

function renderBacktestTeamFilter() {
  ensureBacktestSelectedTeams();
  elements.backtestTeamFilterList.innerHTML = state.backtest.allTeams.map((team) => `
    <label class="team-option">
      <input type="checkbox" value="${escapeHtml(team)}" ${state.backtest.selectedTeams.has(team) ? "checked" : ""}>
      <span>${escapeHtml(team)}</span>
    </label>
  `).join("");
}

function resolveBacktestPlayerName(playerId, rowsBySource) {
  const lookup = state.backtest.dataset?.player_lookup?.[String(playerId)];
  if (lookup?.[0]) {
    return lookup[0];
  }
  for (const rows of rowsBySource) {
    const found = rows.find((row) => String(row.player_id) === String(playerId));
    if (found?.player_name) {
      return found.player_name;
    }
  }
  const predictionSources = Object.values(state.predictor.dataset?.sources || {});
  for (const source of predictionSources) {
    for (const startMap of Object.values(source.predictions || {})) {
      for (const players of Object.values(startMap || {})) {
        const found = players.find((player) => String(player.player_id) === String(playerId));
        if (found) {
          return found.player_name;
        }
      }
    }
  }
  return `Player ${playerId}`;
}

function getBacktestRowsForCurrentWindow({ applySourceFilter = true } = {}) {
  const windowPayload = getActiveDetailWindowPayload();
  if (!windowPayload) {
    return [];
  }
  const rowsBySource = [];
  Object.entries(windowPayload.sources || {}).forEach(([sourceKey, sourcePayload]) => {
    rowsBySource.push(unpackBacktestRows(sourceKey, sourcePayload.rows));
  });
  const playerNameCache = new Map();
  const query = state.backtest.playerQuery.trim().toLowerCase();
  return rowsBySource.flat().map((row) => {
    const playerName = playerNameCache.get(row.player_id) || resolveBacktestPlayerName(row.player_id, rowsBySource);
    playerNameCache.set(row.player_id, playerName);
    return { ...row, player_name: playerName };
  }).filter((row) => {
    if (applySourceFilter && state.backtest.detailSource !== "all" && row.source !== state.backtest.detailSource) {
      return false;
    }
    if (state.backtest.positionFilter !== "ALL" && row.position !== state.backtest.positionFilter) {
      return false;
    }
    if (!state.backtest.selectedTeams.has(row.team)) {
      return false;
    }
    if (!query) {
      return true;
    }
    return `${row.player_name} ${row.team} ${row.position} ${row.source_label}`.toLowerCase().includes(query);
  });
}

function rankRows(rows, valueKey) {
  const sorted = [...rows].sort((left, right) => right[valueKey] - left[valueKey]);
  const ranks = new Map();
  sorted.forEach((row, index) => {
    ranks.set(`${row.source}-${row.player_id}`, index + 1);
  });
  return ranks;
}

function computeBacktestSummary(rows) {
  if (rows.length === 0) {
    return {
      players: 0,
      predicted_points: 0,
      actual_points: 0,
      error: 0,
      absolute_error: 0,
      mae: 0,
      rmse: 0,
      spearman: 0,
      top20_overlap: 0,
    };
  }

  const predRanks = rankRows(rows, "predicted_points");
  const actRanks = rankRows(rows, "actual_points");
  const enrichedRows = rows.map((row) => ({
    ...row,
    predicted_rank_window: predRanks.get(`${row.source}-${row.player_id}`) || 0,
    actual_rank_window: actRanks.get(`${row.source}-${row.player_id}`) || 0,
  }));

  const errors = enrichedRows.map((row) => row.error);
  const absErrors = enrichedRows.map((row) => row.absolute_error);
  const n = enrichedRows.length;
  const diffSq = enrichedRows.reduce((sum, row) => {
    const diff = row.predicted_rank_window - row.actual_rank_window;
    return sum + diff * diff;
  }, 0);
  const spearman = n > 1 ? 1 - ((6 * diffSq) / (n * ((n ** 2) - 1))) : 0;
  const top20Pred = new Set([...enrichedRows].sort((a, b) => b.predicted_points - a.predicted_points).slice(0, 20).map((row) => row.player_id));
  const top20Act = new Set([...enrichedRows].sort((a, b) => b.actual_points - a.actual_points).slice(0, 20).map((row) => row.player_id));
  const overlap = [...top20Pred].filter((id) => top20Act.has(id)).length;

  return {
    players: rows.length,
    predicted_points: rows.reduce((sum, row) => sum + row.predicted_points, 0),
    actual_points: rows.reduce((sum, row) => sum + row.actual_points, 0),
    error: rows.reduce((sum, row) => sum + row.error, 0),
    absolute_error: rows.reduce((sum, row) => sum + row.absolute_error, 0),
    mae: mean(absErrors),
    rmse: Math.sqrt(mean(errors.map((value) => value * value))),
    spearman,
    top20_overlap: overlap,
  };
}

function renderBacktestSummaryCards() {
  const fullRows = getBacktestRowsForCurrentWindow({ applySourceFilter: false });
  if (fullRows.length === 0) {
    elements.backtestSummaryCards.innerHTML = `
      <article class="metric-card">
        <h3>No rows for this window</h3>
        <p class="control-note">Adjust the range or filters to inspect a different historical slice.</p>
      </article>
    `;
    return;
  }

  const sourceOrder = ["official", "elo"];
  elements.backtestSummaryCards.innerHTML = sourceOrder.map((sourceKey) => {
    const rows = fullRows.filter((row) => row.source === sourceKey);
    if (rows.length === 0) {
      return "";
    }
    const summary = computeBacktestSummary(rows);
    const label = state.backtest.dataset.sources[sourceKey];
    const errorClass = summary.error <= 0 ? "metric-good" : "metric-bad";
    return `
      <article class="metric-card">
        <div class="source-kicker">${escapeHtml(label)}</div>
        <div class="metric-main">
          <div>
            <div class="muted">Mean absolute error</div>
            <strong>${formatNumber(summary.mae, 3)}</strong>
          </div>
          <div class="${errorClass}">
            ${formatSigned(summary.error)}
          </div>
        </div>
        <div class="metric-list">
          ${detailRows([
            ["Rows", summary.players],
            ["Predicted points", formatNumber(summary.predicted_points)],
            ["Actual points", formatNumber(summary.actual_points)],
            ["Absolute error", formatNumber(summary.absolute_error)],
            ["RMSE", formatNumber(summary.rmse, 3)],
            ["Spearman", formatNumber(summary.spearman, 4)],
            ["Top-20 overlap", summary.top20_overlap],
          ])}
        </div>
      </article>
    `;
  }).join("");
}

function renderBacktestTrendChart() {
  const dataset = state.backtest.dataset;
  if (!dataset) {
    elements.backtestTrendChart.innerHTML = "";
    elements.backtestSpanChart.innerHTML = "";
    return;
  }
  const selected = getBacktestSelectedGameweeks();
  const horizon = getValidBacktestHorizon();
  const windows = getBacktestHorizonWindows();
  const activeWindow = ensureActiveDetailWindow();
  const selectedKey = activeWindow?.key || null;
  const series = {
    official: [],
    elo: [],
    actual: [],
  };

  windows.forEach((windowEntry) => {
    Object.entries(windowEntry.payload.sources || {}).forEach(([sourceKey, sourcePayload]) => {
      series[sourceKey].push({
        start_gw: windowEntry.start_gw,
        end_gw: windowEntry.end_gw,
        span: horizon,
        key: windowEntry.key,
        mae: Number(sourcePayload.summary?.mae || 0),
        total_points: Number(sourcePayload.summary?.predicted_points || 0),
      });
    });
    const officialSummary = windowEntry.payload.sources?.official?.summary;
    const fallbackSummary = Object.values(windowEntry.payload.sources || {})[0]?.summary;
    const actualSummary = officialSummary || fallbackSummary;
    if (actualSummary) {
      series.actual.push({
        start_gw: windowEntry.start_gw,
        end_gw: windowEntry.end_gw,
        span: horizon,
        key: windowEntry.key,
        mae: 0,
        total_points: Number(actualSummary.actual_points || 0),
      });
    }
  });

  const allPoints = [...series.official, ...series.elo];
  if (allPoints.length === 0) {
    elements.backtestTrendChart.innerHTML = `<div class="empty-state">No trend data is available inside the selected gameweek range.</div>`;
    elements.backtestSpanChart.innerHTML = `<div class="empty-state">No span summary is available inside the selected gameweek range.</div>`;
    return;
  }

  elements.backtestTrendChart.innerHTML = buildLineChart({
    series: {
      official: series.official.map((point) => ({ ...point, value: point.total_points })),
      elo: series.elo.map((point) => ({ ...point, value: point.total_points })),
      actual: series.actual.map((point) => ({ ...point, value: point.total_points })),
    },
    selectedKey,
    xAccessor: (point) => point.start_gw,
    xFormatter: (value) => `GW${value}`,
    titlePrefix: (point) => `GW${point.start_gw}-${point.end_gw}`,
    ariaLabel: "Projected and actual total points by start gameweek",
  });

  const spanSeries = {
    official: [],
    elo: [],
  };
  const rangeWindows = getBacktestRangeWindows();
  ["official", "elo"].forEach((sourceKey) => {
    const grouped = new Map();
    rangeWindows.forEach((windowEntry) => {
      const sourcePayload = windowEntry.sources?.[sourceKey];
      if (!sourcePayload) {
        return;
      }
      if (!grouped.has(windowEntry.span)) {
        grouped.set(windowEntry.span, []);
      }
      grouped.get(windowEntry.span).push(Number(sourcePayload.summary?.mae || 0));
    });
    spanSeries[sourceKey] = [...grouped.entries()]
      .map(([spanValue, values]) => ({
        span: spanValue,
        value: mean(values),
        windows: values.length,
      }))
      .sort((left, right) => left.span - right.span);
  });

  elements.backtestSpanChart.innerHTML = buildLineChart({
    series: spanSeries,
    selectedKey: null,
    xAccessor: (point) => point.span,
    xFormatter: (value) => `${value} GW`,
    titlePrefix: (point) => `${point.span}-GW average across ${point.windows} window${point.windows === 1 ? "" : "s"}`,
    ariaLabel: "Average MAE by span",
  });

  const distinctStarts = [...new Set(windows.map((point) => point.start_gw))].sort((a, b) => a - b);
  elements.backtestTrendNote.textContent = distinctStarts.length === 1
    ? `Only one valid historical start gameweek fits inside the selected range: GW${distinctStarts[0]}. Shorten the range to compare more start windows.`
    : `This chart shows every rolling ${horizon}-gameweek window that starts between GW${selected.start} and GW${selected.end - horizon + 1}.`;
  elements.backtestSpanNote.textContent = "This chart averages all valid windows inside the selected range by span length and plots MAE for Official and Elo.";
}

function buildLineChart({ series, selectedKey, xAccessor, xFormatter, titlePrefix, ariaLabel }) {
  const allPoints = [...(series.official || []), ...(series.elo || []), ...(series.actual || [])];
  if (allPoints.length === 0) {
    return `<div class="empty-state">No chart data is available.</div>`;
  }

  const width = 860;
  const height = 240;
  const margin = { top: 16, right: 18, bottom: 34, left: 44 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const xValues = [...new Set(allPoints.map((point) => xAccessor(point)))].sort((a, b) => a - b);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues);
  const maxY = Math.max(...allPoints.map((point) => Number(point.value ?? point.mae ?? 0)), 0.1);

  function xScale(value) {
    if (minX === maxX) {
      return margin.left + chartWidth / 2;
    }
    return margin.left + ((value - minX) / (maxX - minX)) * chartWidth;
  }

  function yScale(value) {
    return margin.top + chartHeight - (value / maxY) * chartHeight;
  }

  function buildSeries(points, color) {
    if (!points.length) {
      return "";
    }
    const sorted = [...points].sort((a, b) => xAccessor(a) - xAccessor(b));
    const path = sorted.map((point, index) => `${index === 0 ? "M" : "L"} ${xScale(xAccessor(point))} ${yScale(Number(point.value ?? point.mae ?? 0))}`).join(" ");
    const dots = sorted.map((point) => `
      <circle data-detail-start="${point.start_gw ?? ""}" cx="${xScale(xAccessor(point))}" cy="${yScale(Number(point.value ?? point.mae ?? 0))}" r="${selectedKey && point.key === selectedKey ? 5 : 3.5}" fill="${color}" opacity="${selectedKey && point.key === selectedKey ? 1 : 0.85}"></circle>
      <title>${titlePrefix(point)}: ${formatNumber(Number(point.value ?? point.mae ?? 0), 3)}</title>
    `).join("");
    return `<path d="${path}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></path>${dots}`;
  }

  const xLabels = xValues
    .map((value) => `<text x="${xScale(value)}" y="${height - 10}" text-anchor="middle" font-size="11" fill="#576074">${xFormatter(value)}</text>`)
    .join("");
  const yLabels = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const value = maxY * ratio;
    const y = yScale(value);
    return `
      <line x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}" stroke="rgba(87, 96, 116, 0.15)"></line>
      <text x="${margin.left - 10}" y="${y + 4}" text-anchor="end" font-size="11" fill="#576074">${formatNumber(value, 2)}</text>
    `;
  }).join("");

  return `
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(ariaLabel)}">
      ${yLabels}
      ${buildSeries(series.official || [], "#14213d")}
      ${buildSeries(series.elo || [], "#ff7a00")}
      ${buildSeries(series.actual || [], "#1b7f5b")}
      ${xLabels}
    </svg>
  `;
}

function renderBacktestBreakdownTable() {
  const rows = getBacktestRowsForCurrentWindow({ applySourceFilter: true });
  if (rows.length === 0) {
    elements.backtestBreakdownBody.innerHTML = `<tr><td colspan="9">No grouped rows are available for this filter combination.</td></tr>`;
    return;
  }

  const grouped = new Map();
  rows.forEach((row) => {
    const groupBy = state.backtest.groupBy;
    const groupKey = groupBy === "team"
      ? row.team
      : groupBy === "position"
        ? row.position
        : groupBy === "source"
          ? row.source_label
          : row.player_name;
    const key = `${row.source}|${groupKey}`;
    if (!grouped.has(key)) {
      grouped.set(key, {
        group: groupKey,
        source: row.source_label,
        rows: [],
      });
    }
    grouped.get(key).rows.push(row);
  });

  const groups = [...grouped.values()].map((group) => {
    const summary = computeBacktestSummary(group.rows);
    return {
      ...group,
      ...summary,
      avg_rank_error: mean(group.rows.map((row) => Math.abs(row.rank_error))),
    };
  }).sort((left, right) => right.absolute_error - left.absolute_error);

  elements.backtestBreakdownBody.innerHTML = groups.map((group) => `
    <tr>
      <td>${escapeHtml(group.group)}</td>
      <td>${escapeHtml(group.source)}</td>
      <td>${group.players}</td>
      <td>${formatNumber(group.predicted_points)}</td>
      <td>${formatNumber(group.actual_points)}</td>
      <td>${formatSigned(group.error)}</td>
      <td>${formatNumber(group.absolute_error)}</td>
      <td>${formatNumber(group.mae, 3)}</td>
      <td>${formatNumber(group.avg_rank_error, 2)}</td>
    </tr>
  `).join("");
}

function backtestSortValue(row, sortKey) {
  switch (sortKey) {
    case "player":
      return row.player_name;
    case "source":
      return row.source_label;
    case "team":
      return row.team;
    case "position":
      return row.position;
    default:
      return Number(row[sortKey] || 0);
  }
}

function updateBacktestSortButtons() {
  elements.backtestSortButtons.forEach((button) => {
    button.dataset.direction = button.dataset.backtestSort === state.backtest.sortKey ? state.backtest.sortDirection : "";
  });
}

function renderBacktestRowsTable() {
  const rows = [...getBacktestRowsForCurrentWindow({ applySourceFilter: true })];
  const direction = state.backtest.sortDirection === "asc" ? 1 : -1;
  rows.sort((left, right) => {
    const leftValue = backtestSortValue(left, state.backtest.sortKey);
    const rightValue = backtestSortValue(right, state.backtest.sortKey);
    if (typeof leftValue === "string" || typeof rightValue === "string") {
      return leftValue.localeCompare(rightValue) * direction;
    }
    if (leftValue === rightValue) {
      return right.absolute_error - left.absolute_error;
    }
    return (leftValue - rightValue) * direction;
  });

  if (rows.length === 0) {
    elements.backtestRowsBody.innerHTML = `<tr><td colspan="10">No player rows are available for this filter combination.</td></tr>`;
    updateBacktestSortButtons();
    return;
  }

  elements.backtestRowsBody.innerHTML = rows.map((row) => `
    <tr>
      <td>
        <button class="player-button" type="button" data-backtest-player-id="${row.player_id}">
          <strong>${escapeHtml(row.player_name)}</strong>
        </button>
      </td>
      <td>${escapeHtml(row.source_label)}</td>
      <td>${escapeHtml(row.team)}</td>
      <td>${escapeHtml(row.position)}</td>
      <td>${formatNumber(row.predicted_points)}</td>
      <td>${formatNumber(row.actual_points)}</td>
      <td>${formatSigned(row.error)}</td>
      <td>${formatNumber(row.absolute_error)}</td>
      <td>${row.predicted_rank}</td>
      <td>${row.actual_rank}</td>
    </tr>
  `).join("");

  updateBacktestSortButtons();
}

function sourceRowsForWindow(windowPayload, sourceKey) {
  const sourcePayload = windowPayload?.sources?.[sourceKey];
  return sourcePayload?.rows ? unpackBacktestRows(sourceKey, sourcePayload.rows) : [];
}

function aggregateComponentRows(rows) {
  const totals = {
    predicted_points: 0,
    actual_points: 0,
    predicted_components: {
      minutes_points: 0,
      goal_points: 0,
      assist_points: 0,
      clean_sheet_points: 0,
      defensive_contribution_points: 0,
      bonus_points: 0,
      yellow_deduction: 0,
      other_points: 0,
    },
    actual_components: {
      minutes_points: 0,
      goal_points: 0,
      assist_points: 0,
      clean_sheet_points: 0,
      defensive_contribution_points: 0,
      bonus_points: 0,
      yellow_deduction: 0,
      other_points: 0,
    },
    predicted_stats: {
      goals: 0,
      assists: 0,
      clean_sheets: 0,
      bonus: 0,
      yellow_cards: 0,
      expected_goals: 0,
      expected_assists: 0,
      defensive_contribution: 0,
    },
    actual_stats: {
      goals: 0,
      assists: 0,
      clean_sheets: 0,
      bonus: 0,
      yellow_cards: 0,
      expected_goals: 0,
      expected_assists: 0,
      defensive_contribution: 0,
    },
  };

  rows.forEach((row) => {
    totals.predicted_points += row.predicted_points;
    totals.actual_points += row.actual_points;
    Object.keys(totals.predicted_components).forEach((key) => {
      totals.predicted_components[key] += row.predicted_components[key] || 0;
      totals.actual_components[key] += row.actual_components[key] || 0;
    });
    Object.keys(totals.predicted_stats).forEach((key) => {
      totals.predicted_stats[key] += row.predicted_stats[key] || 0;
      totals.actual_stats[key] += row.actual_stats[key] || 0;
    });
  });
  return totals;
}

function emptyAttribution() {
  return aggregateComponentRows([]);
}

function attributionRows(official, elo) {
  const metrics = [
    ["Total points", official.predicted_points, official.actual_points, elo.predicted_points, elo.actual_points],
    ["Minutes points", official.predicted_components.minutes_points, official.actual_components.minutes_points, elo.predicted_components.minutes_points, elo.actual_components.minutes_points],
    ["Goal points", official.predicted_components.goal_points, official.actual_components.goal_points, elo.predicted_components.goal_points, elo.actual_components.goal_points],
    ["Assist points", official.predicted_components.assist_points, official.actual_components.assist_points, elo.predicted_components.assist_points, elo.actual_components.assist_points],
    ["Clean-sheet points", official.predicted_components.clean_sheet_points, official.actual_components.clean_sheet_points, elo.predicted_components.clean_sheet_points, elo.actual_components.clean_sheet_points],
    ["Defcon points", official.predicted_components.defensive_contribution_points, official.actual_components.defensive_contribution_points, elo.predicted_components.defensive_contribution_points, elo.actual_components.defensive_contribution_points],
    ["Bonus points", official.predicted_components.bonus_points, official.actual_components.bonus_points, elo.predicted_components.bonus_points, elo.actual_components.bonus_points],
    ["Yellow deduction", official.predicted_components.yellow_deduction, official.actual_components.yellow_deduction, elo.predicted_components.yellow_deduction, elo.actual_components.yellow_deduction],
    ["Other points", official.predicted_components.other_points, official.actual_components.other_points, elo.predicted_components.other_points, elo.actual_components.other_points],
    ["Pred goals vs actual goals", official.predicted_stats.goals, official.actual_stats.goals, elo.predicted_stats.goals, elo.actual_stats.goals],
    ["Pred assists vs actual assists", official.predicted_stats.assists, official.actual_stats.assists, elo.predicted_stats.assists, elo.actual_stats.assists],
    ["Pred clean sheets vs actual", official.predicted_stats.clean_sheets, official.actual_stats.clean_sheets, elo.predicted_stats.clean_sheets, elo.actual_stats.clean_sheets],
    ["Pred bonus vs actual bonus", official.predicted_stats.bonus, official.actual_stats.bonus, elo.predicted_stats.bonus, elo.actual_stats.bonus],
    ["Pred yellows vs actual", official.predicted_stats.yellow_cards, official.actual_stats.yellow_cards, elo.predicted_stats.yellow_cards, elo.actual_stats.yellow_cards],
    ["Pred xG-ish vs actual xG", official.predicted_stats.expected_goals, official.actual_stats.expected_goals, elo.predicted_stats.expected_goals, elo.actual_stats.expected_goals],
    ["Pred xA-ish vs actual xA", official.predicted_stats.expected_assists, official.actual_stats.expected_assists, elo.predicted_stats.expected_assists, elo.actual_stats.expected_assists],
    ["Pred defcon vs actual defcon", official.predicted_stats.defensive_contribution, official.actual_stats.defensive_contribution, elo.predicted_stats.defensive_contribution, elo.actual_stats.defensive_contribution],
  ];

  return metrics.map(([label, op, oa, ep, ea]) => `
    <tr>
      <td>${escapeHtml(label)}</td>
      <td>${formatNumber(op, 2)}</td>
      <td>${formatNumber(oa, 2)}</td>
      <td>${formatNumber(ep, 2)}</td>
      <td>${formatNumber(ea, 2)}</td>
    </tr>
  `).join("");
}

function renderBacktestAttributionTables() {
  const detailWindow = ensureActiveDetailWindow();
  if (!detailWindow) {
    elements.backtestDetailWindowStatus.textContent = "No valid detail window for the current range and horizon.";
    elements.backtestDetailComponentsBody.innerHTML = "";
    elements.backtestAggregateStatus.textContent = "No valid aggregate windows for the current range and horizon.";
    elements.backtestAggregateComponentsBody.innerHTML = "";
    return;
  }

  const detailPayload = getActiveDetailWindowPayload();
  const detailOfficial = detailPayload?.sources?.official?.attribution || emptyAttribution();
  const detailElo = detailPayload?.sources?.elo?.attribution || emptyAttribution();
  elements.backtestDetailWindowStatus.textContent = `Detail window: GW${detailWindow.start_gw} to GW${detailWindow.end_gw}. Click a point on the trend chart to inspect a different start gameweek.`;
  elements.backtestDetailComponentsBody.innerHTML = attributionRows(detailOfficial, detailElo);

  const horizonWindows = getBacktestHorizonWindows();
  const aggregateOfficial = emptyAttribution();
  const aggregateElo = emptyAttribution();
  horizonWindows.forEach((windowEntry) => {
    const official = windowEntry.payload?.sources?.official?.attribution;
    const elo = windowEntry.payload?.sources?.elo?.attribution;
    if (official) {
      aggregateOfficial.predicted_points += official.predicted_points || 0;
      aggregateOfficial.actual_points += official.actual_points || 0;
      Object.keys(aggregateOfficial.predicted_components).forEach((key) => {
        aggregateOfficial.predicted_components[key] += official.predicted_components?.[key] || 0;
        aggregateOfficial.actual_components[key] += official.actual_components?.[key] || 0;
      });
      Object.keys(aggregateOfficial.predicted_stats).forEach((key) => {
        aggregateOfficial.predicted_stats[key] += official.predicted_stats?.[key] || 0;
        aggregateOfficial.actual_stats[key] += official.actual_stats?.[key] || 0;
      });
    }
    if (elo) {
      aggregateElo.predicted_points += elo.predicted_points || 0;
      aggregateElo.actual_points += elo.actual_points || 0;
      Object.keys(aggregateElo.predicted_components).forEach((key) => {
        aggregateElo.predicted_components[key] += elo.predicted_components?.[key] || 0;
        aggregateElo.actual_components[key] += elo.actual_components?.[key] || 0;
      });
      Object.keys(aggregateElo.predicted_stats).forEach((key) => {
        aggregateElo.predicted_stats[key] += elo.predicted_stats?.[key] || 0;
        aggregateElo.actual_stats[key] += elo.actual_stats?.[key] || 0;
      });
    }
  });
  elements.backtestAggregateStatus.textContent = `Aggregate across ${horizonWindows.length} rolling ${getValidBacktestHorizon()}-GW window${horizonWindows.length === 1 ? "" : "s"} in the selected range.`;
  elements.backtestAggregateComponentsBody.innerHTML = attributionRows(aggregateOfficial, aggregateElo);
}

function openBacktestPlayerModal(playerId) {
  const detailWindow = ensureActiveDetailWindow();
  const windowPayload = detailWindow?.payload;
  if (!windowPayload) {
    return;
  }

  const compared = Object.entries(windowPayload.sources || {})
    .map(([sourceKey, sourcePayload]) => {
      const row = unpackBacktestRows(sourceKey, sourcePayload.rows)
        .map((item) => ({
          ...item,
          player_name: resolveBacktestPlayerName(item.player_id, [unpackBacktestRows(sourceKey, sourcePayload.rows)]),
        }))
        .find((item) => String(item.player_id) === String(playerId));
      return { sourceKey, label: state.backtest.dataset.sources[sourceKey], row };
    })
    .filter((entry) => entry.row);

  if (compared.length === 0) {
    return;
  }

  const player = compared[0].row;
  elements.modalTitle.textContent = player.player_name;
  elements.modalSubtitle.textContent = `${player.team} · ${player.position} · Backtest GW${detailWindow.start_gw} to GW${detailWindow.end_gw}`;
  elements.modalContent.innerHTML = compared.map(({ label, row }) => `
    <section class="stack">
      <div class="source-kicker">${escapeHtml(label)}</div>
      <article class="detail-card">
        <h3>Accuracy</h3>
        <div class="metric-list">
          ${detailRows([
            ["Predicted points", formatNumber(row.predicted_points)],
            ["Actual points", formatNumber(row.actual_points)],
            ["Error", formatSigned(row.error)],
            ["Absolute error", formatNumber(row.absolute_error)],
            ["Predicted rank", row.predicted_rank],
            ["Actual rank", row.actual_rank],
            ["Rank error", formatSigned(row.rank_error, 0)],
          ])}
        </div>
      </article>
    </section>
  `).join("");
  elements.playerModal.hidden = false;
}

function refreshBacktestView() {
  if (!state.backtest.dataset) {
    return;
  }
  const detailWindow = ensureActiveDetailWindow();
  renderBacktestTeamFilter();
  renderBacktestRangeLabels();
  renderBacktestRangeFill();
  updateBacktestRangeSummary();
  renderBacktestSummaryCards();
  renderBacktestTrendChart();
  renderBacktestAttributionTables();
  renderBacktestBreakdownTable();
  renderBacktestRowsTable();

  const generatedAt = state.backtest.dataset.generated_at ? new Date(state.backtest.dataset.generated_at).toLocaleString() : "unknown time";
  const selected = getBacktestSelectedGameweeks();
  const audit = detailWindow?.payload?.audit || {};
  const horizon = getValidBacktestHorizon();
  const auditText = audit.common_players
    ? ` Official vs Elo: ${audit.different_prediction_matches} of ${audit.common_players} player predictions differ in this window (max delta ${formatNumber(audit.max_prediction_delta)}).`
    : "";
  const detailText = detailWindow ? ` Active detail window: GW${detailWindow.start_gw} to GW${detailWindow.end_gw}.` : "";
  elements.backtestStatusText.textContent = `Showing rolling ${horizon}-GW projections from GW${selected.start} to GW${selected.end} from the ${generatedAt} backtest snapshot.${detailText}${auditText}`;
}

async function loadBacktestData() {
  if (state.backtest.isLoading || state.backtest.hasLoaded) {
    return;
  }
  state.backtest.isLoading = true;
  const dataUrl = window.FPL_BACKTEST_DATA_URL || "./data/static_backtest.json";
  elements.backtestStatusText.textContent = "Loading static backtest data...";
  try {
    const response = await fetch(dataUrl, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Static backtest request failed");
    }
    state.backtest.dataset = payload;
    state.backtest.selectedTeams = new Set();
    state.backtest.teamsInitialized = false;
    state.backtest.windowDetails = {};
    buildBacktestAllTeams();
    configureBacktestRangeControl();
    renderBacktestTeamFilter();
    refreshBacktestView();
    state.backtest.hasLoaded = true;
  } catch (error) {
    elements.backtestStatusText.textContent = `Static backtest load failed: ${error.message}`;
    elements.backtestSummaryCards.innerHTML = "";
    elements.backtestTrendChart.innerHTML = "";
    elements.backtestSpanChart.innerHTML = "";
    elements.backtestBreakdownBody.innerHTML = "";
    elements.backtestRowsBody.innerHTML = "";
  } finally {
    state.backtest.isLoading = false;
  }
}

function ensureBacktestViewLoaded() {
  if (state.backtest.hasLoaded || state.backtest.isLoading) {
    return;
  }
  elements.backtestStatusText.textContent = "Opening backtest workspace...";
  window.setTimeout(() => {
    loadBacktestData();
    detectLocalApi();
  }, 0);
}

async function detectLocalApi() {
  if (state.backtest.localAvailable) {
    return;
  }
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Health check failed");
    }
    state.backtest.localAvailable = true;
    elements.backtestLocalStatus.textContent = "Local API detected. You can recompute the selected window on demand.";
    elements.backtestRecomputeButton.disabled = false;
  } catch (error) {
    state.backtest.localAvailable = false;
    elements.backtestLocalStatus.textContent = "Static mode only on this host. Start server.py to enable local recompute.";
    elements.backtestRecomputeButton.disabled = true;
  }
}

async function recomputeBacktestWindow() {
  if (!state.backtest.localAvailable) {
    return;
  }
  const selected = getBacktestSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    return;
  }
  const url = `/api/backtest?start_gw=${selected.start}&end_gw=${selected.end}&recompute=1`;
  elements.backtestStatusText.textContent = `Recomputing GW${selected.start} to GW${selected.end} via local API...`;
  try {
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Local backtest recompute failed");
    }
    state.backtest.windowOverrides[getCurrentBacktestWindowKey()] = {
      sources: payload.sources || {},
      audit: payload.audit || {},
    };
    elements.backtestStatusText.textContent = `Recomputed GW${selected.start} to GW${selected.end} from the local API snapshot.`;
    refreshBacktestView();
  } catch (error) {
    elements.backtestStatusText.textContent = `Local recompute failed: ${error.message}`;
  }
}

elements.viewButtons.forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    updateViewUrl(button.dataset.view);
    switchView(button.dataset.view);
  });
});

elements.startGw.addEventListener("input", () => {
  applyPredictorStartBounds();
  refreshPredictorView();
});

elements.endGw.addEventListener("input", () => {
  applyPredictorEndBounds();
  refreshPredictorView();
});

elements.showBonus.addEventListener("change", refreshPredictorView);
elements.showYellows.addEventListener("change", refreshPredictorView);
elements.refreshButton.addEventListener("click", loadPredictions);
elements.positionFilter.addEventListener("change", refreshPredictorView);

elements.teamFilterList.addEventListener("change", (event) => {
  const input = event.target.closest("input[type='checkbox']");
  if (!input) {
    return;
  }
  if (input.checked) {
    state.predictor.selectedTeams.add(input.value);
  } else {
    state.predictor.selectedTeams.delete(input.value);
  }
  refreshPredictorView();
});

elements.selectAllTeamsButton.addEventListener("click", () => {
  state.predictor.selectedTeams = new Set(getPredictorAllTeams());
  refreshPredictorView();
});

elements.clearAllTeamsButton.addEventListener("click", () => {
  state.predictor.selectedTeams = new Set();
  refreshPredictorView();
});

elements.sortButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const selectedKey = button.dataset.sort;
    if (state.predictor.sortKey === selectedKey) {
      state.predictor.sortDirection = state.predictor.sortDirection === "desc" ? "asc" : "desc";
    } else {
      state.predictor.sortKey = selectedKey;
      state.predictor.sortDirection = "desc";
    }
    renderPredictorTable();
  });
});

elements.sourceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.predictor.activeSource = button.dataset.source;
    updatePredictorSourceButtons();
    refreshPredictorView();
  });
});

elements.resultsBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-player-id]");
  if (button) {
    openPredictorPlayerModal(button.dataset.playerId);
  }
});

elements.backtestStartGw.addEventListener("input", () => {
  applyBacktestStartBounds();
  state.backtest.activeDetailStartGw = null;
  refreshBacktestView();
});

elements.backtestEndGw.addEventListener("input", () => {
  applyBacktestEndBounds();
  state.backtest.activeDetailStartGw = null;
  refreshBacktestView();
});

elements.backtestHorizonInput.addEventListener("input", () => {
  state.backtest.horizon = Number(elements.backtestHorizonInput.value) || 1;
  state.backtest.activeDetailStartGw = null;
  refreshBacktestView();
});

elements.backtestPositionFilter.addEventListener("change", () => {
  state.backtest.positionFilter = elements.backtestPositionFilter.value;
  refreshBacktestView();
});

elements.backtestGroupBy.addEventListener("change", () => {
  state.backtest.groupBy = elements.backtestGroupBy.value;
  refreshBacktestView();
});

elements.backtestPlayerSearch.addEventListener("input", () => {
  state.backtest.playerQuery = elements.backtestPlayerSearch.value;
  refreshBacktestView();
});

elements.backtestSourceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.backtest.detailSource = button.dataset.backtestSource;
    elements.backtestSourceButtons.forEach((item) => {
      item.classList.toggle("is-active", item.dataset.backtestSource === state.backtest.detailSource);
    });
    refreshBacktestView();
  });
});

elements.backtestTeamFilterList.addEventListener("change", (event) => {
  const input = event.target.closest("input[type='checkbox']");
  if (!input) {
    return;
  }
  if (input.checked) {
    state.backtest.selectedTeams.add(input.value);
  } else {
    state.backtest.selectedTeams.delete(input.value);
  }
  refreshBacktestView();
});

elements.backtestSelectAllTeamsButton.addEventListener("click", () => {
  state.backtest.selectedTeams = new Set(state.backtest.allTeams);
  refreshBacktestView();
});

elements.backtestClearAllTeamsButton.addEventListener("click", () => {
  state.backtest.selectedTeams = new Set();
  refreshBacktestView();
});

elements.backtestSortButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const selectedKey = button.dataset.backtestSort;
    if (state.backtest.sortKey === selectedKey) {
      state.backtest.sortDirection = state.backtest.sortDirection === "desc" ? "asc" : "desc";
    } else {
      state.backtest.sortKey = selectedKey;
      state.backtest.sortDirection = "desc";
    }
    renderBacktestRowsTable();
  });
});

elements.backtestRowsBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-backtest-player-id]");
  if (button) {
    openBacktestPlayerModal(button.dataset.backtestPlayerId);
  }
});

elements.backtestTrendChart.addEventListener("click", (event) => {
  const point = event.target.closest("[data-detail-start]");
  if (!point || !point.dataset.detailStart) {
    return;
  }
  state.backtest.activeDetailStartGw = Number(point.dataset.detailStart);
  refreshBacktestView();
});

elements.backtestRecomputeButton.addEventListener("click", recomputeBacktestWindow);
elements.closeModalButton.addEventListener("click", closeModal);
elements.playerModal.addEventListener("click", (event) => {
  if (event.target === elements.playerModal) {
    closeModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.playerModal.hidden) {
    closeModal();
  }
});

updateOptionalColumns();
switchView(new URLSearchParams(window.location.search).get("view") === "backtest" ? "backtest" : "predictor");
loadPredictions();
