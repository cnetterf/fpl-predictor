const state = {
  dataset: null,
  players: [],
  availableGameweeks: [],
  activeSource: "official",
  sortKey: "total",
  sortDirection: "desc",
  activePlayer: null,
};

const elements = {
  startGw: document.getElementById("startGw"),
  endGw: document.getElementById("endGw"),
  rangeValue: document.getElementById("rangeValue"),
  rangeSpan: document.getElementById("rangeSpan"),
  rangeFill: document.getElementById("rangeFill"),
  horizonLabels: document.getElementById("horizonLabels"),
  positionFilter: document.getElementById("positionFilter"),
  showBonus: document.getElementById("showBonus"),
  showYellows: document.getElementById("showYellows"),
  refreshButton: document.getElementById("refreshButton"),
  sourceButtons: document.querySelectorAll("[data-source]"),
  playerCount: document.getElementById("playerCount"),
  statusText: document.getElementById("statusText"),
  resultsBody: document.getElementById("resultsBody"),
  optionalHeaders: document.querySelectorAll("[data-optional]"),
  sortButtons: document.querySelectorAll("[data-sort]"),
  playerModal: document.getElementById("playerModal"),
  modalTitle: document.getElementById("modalTitle"),
  modalSubtitle: document.getElementById("modalSubtitle"),
  modalContent: document.getElementById("modalContent"),
  closeModalButton: document.getElementById("closeModalButton"),
};

function formatSigned(value) {
  return Number(value).toFixed(2);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function getStartIndex() {
  return Number(elements.startGw.value);
}

function getEndIndex() {
  return Number(elements.endGw.value);
}

function getSelectedGameweeks() {
  const startIndex = getStartIndex();
  const endIndex = getEndIndex();
  return {
    start: state.availableGameweeks[startIndex] ?? null,
    end: state.availableGameweeks[endIndex] ?? null,
    span: endIndex - startIndex + 1,
  };
}

function getSourceData() {
  return state.dataset?.sources?.[state.activeSource] || null;
}

function fixtureLabel(fixture) {
  return `GW${fixture.event}: ${fixture.opponent} (${fixture.home ? "H" : "A"}, diff ${fixture.difficulty})`;
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

function updateOptionalColumns() {
  const mapping = {
    bonus: elements.showBonus.checked,
    yellow: elements.showYellows.checked,
  };

  elements.optionalHeaders.forEach((header) => {
    const key = header.dataset.optional;
    header.style.display = mapping[key] ? "" : "none";
  });

  document.querySelectorAll("[data-cell-optional]").forEach((cell) => {
    const key = cell.dataset.cellOptional;
    cell.style.display = mapping[key] ? "" : "none";
  });
}

function renderRangeLabels() {
  const labels = state.availableGameweeks.map((gameweek, index) => {
    const selected = index >= getStartIndex() && index <= getEndIndex();
    const weight = selected ? "700" : "400";
    return `<span style="font-weight:${weight}">GW${gameweek}</span>`;
  });
  elements.horizonLabels.innerHTML = labels.join("");
}

function renderRangeFill() {
  const maxIndex = Math.max(state.availableGameweeks.length - 1, 1);
  const startPercent = (getStartIndex() / maxIndex) * 100;
  const endPercent = (getEndIndex() / maxIndex) * 100;
  elements.rangeFill.style.left = `${startPercent}%`;
  elements.rangeFill.style.width = `${Math.max(endPercent - startPercent, 0)}%`;
}

function updateRangeSummary() {
  const selected = getSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    elements.rangeValue.textContent = "No gameweeks available";
    elements.rangeSpan.textContent = "";
    return;
  }
  elements.rangeValue.textContent = `GW ${selected.start} to GW ${selected.end}`;
  elements.rangeSpan.textContent = `${selected.span} week${selected.span === 1 ? "" : "s"}`;
}

function applyStartBounds() {
  const maxIndex = state.availableGameweeks.length - 1;
  const startIndex = getStartIndex();
  let endIndex = getEndIndex();

  if (startIndex > endIndex) {
    endIndex = startIndex;
  }
  if (endIndex - startIndex >= 6) {
    endIndex = Math.min(startIndex + 5, maxIndex);
  }

  elements.endGw.value = String(endIndex);
}

function applyEndBounds() {
  let startIndex = getStartIndex();
  const endIndex = getEndIndex();

  if (endIndex < startIndex) {
    startIndex = endIndex;
  }
  if (endIndex - startIndex >= 6) {
    startIndex = endIndex - 5;
  }

  elements.startGw.value = String(startIndex);
}

function configureRangeControl() {
  const gameweeks = state.dataset?.available_gameweeks || [];
  state.availableGameweeks = gameweeks;

  const maxIndex = Math.max(gameweeks.length - 1, 0);
  elements.startGw.min = "0";
  elements.startGw.max = String(maxIndex);
  elements.endGw.min = "0";
  elements.endGw.max = String(maxIndex);
  elements.startGw.value = "0";
  elements.endGw.value = String(Math.min(maxIndex, 5));

  renderRangeLabels();
  renderRangeFill();
  updateRangeSummary();
}

function updateSourceButtons() {
  elements.sourceButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.source === state.activeSource);
  });
}

function getWindowPlayers() {
  const sourceData = getSourceData();
  if (!state.dataset || !sourceData || state.availableGameweeks.length === 0) {
    return [];
  }

  const selected = getSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    return [];
  }

  const startBucket = (sourceData.predictions || {})[String(selected.start)] || {};
  const rows = startBucket[String(selected.end)] || [];
  if (elements.positionFilter.value === "ALL") {
    return rows;
  }
  return rows.filter((player) => player.position === elements.positionFilter.value);
}

function sortValue(player, sortKey) {
  const components = player.components;
  const mapping = {
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
  };
  return mapping[sortKey];
}

function comparePlayers(left, right) {
  const leftValue = sortValue(left, state.sortKey);
  const rightValue = sortValue(right, state.sortKey);
  const direction = state.sortDirection === "asc" ? 1 : -1;

  if (typeof leftValue === "string" || typeof rightValue === "string") {
    return leftValue.localeCompare(rightValue) * direction;
  }
  if (leftValue === rightValue) {
    return Number(right.predicted_total_points) - Number(left.predicted_total_points);
  }
  return (leftValue - rightValue) * direction;
}

function updateSortButtons() {
  elements.sortButtons.forEach((button) => {
    button.dataset.direction = button.dataset.sort === state.sortKey ? state.sortDirection : "";
  });
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

function openPlayerModal(playerId) {
  const player = state.players.find((item) => String(item.player_id) === String(playerId));
  if (!player) {
    return;
  }

  state.activePlayer = player;
  const inputs = player.inputs || {};
  const goalModel = inputs.goal_model || {};
  const assistModel = inputs.assist_model || {};

  elements.modalTitle.textContent = player.player_name;
  elements.modalSubtitle.textContent = `${player.team} · ${player.position} · ${getSourceData()?.label || ""} · ${player.fixtures.map(fixtureLabel).join(" / ")}`;
  elements.modalContent.innerHTML = `
    <article class="detail-card">
      <h3>Total</h3>
      <div class="detail-list">
        ${detailRows([
          ["Displayed total", formatSigned(displayedTotalPoints(player))],
          ["Model total", formatSigned(player.predicted_total_points)],
          ["Minutes points", formatSigned(player.components.minutes_points)],
          ["Goal points", formatSigned(player.components.goal_points)],
          ["Assist points", formatSigned(player.components.assist_points)],
          ["Clean sheet points", formatSigned(player.components.clean_sheet_points)],
          ["Defensive points", formatSigned(player.components.defensive_contribution_points)],
          ["Bonus points", formatSigned(player.components.bonus_points)],
          ["Yellow-card deduction", formatSigned(player.components.yellow_cards)],
        ])}
      </div>
    </article>
    <article class="detail-card">
      <h3>Minutes</h3>
      <div class="detail-list">
        ${detailRows([
          ["Predicted minutes / fixture", formatSigned(inputs.predicted_minutes_per_fixture || 0)],
          ["Minutes points / fixture", formatSigned(inputs.minutes_points_per_fixture || 0)],
          ["Base recent-minutes average", formatSigned(inputs.minutes_base || 0)],
          ["Availability factor", formatSigned(inputs.availability_factor || 0)],
          ["Rotation factor", formatSigned(inputs.rotation_factor || 0)],
        ])}
      </div>
      ${matchesMarkup(inputs.minutes_sample)}
    </article>
    <article class="detail-card">
      <h3>Goals</h3>
      <div class="detail-list">
        ${detailRows([
          ["Predicted goals / fixture", formatSigned(inputs.goals_per_fixture || 0)],
          ["Recent xG total", formatSigned(goalModel.recent_xg_total || 0)],
          ["Recent goals total", formatSigned(goalModel.recent_goals_total || 0)],
          ["Baseline / fixture", formatSigned(goalModel.baseline_per_fixture || 0)],
          ["Finishing adjustment", formatSigned(goalModel.finishing_adjustment || 0)],
          ["Fixture factor", formatSigned(goalModel.fixture_factor || 0)],
        ])}
      </div>
    </article>
    <article class="detail-card">
      <h3>Assists</h3>
      <div class="detail-list">
        ${detailRows([
          ["Predicted assists / fixture", formatSigned(inputs.assists_per_fixture || 0)],
          ["Recent xA total", formatSigned(assistModel.recent_xa_total || 0)],
          ["Recent assists total", formatSigned(assistModel.recent_assists_total || 0)],
          ["Baseline / fixture", formatSigned(assistModel.baseline_per_fixture || 0)],
          ["Conversion adjustment", formatSigned(assistModel.conversion_adjustment || 0)],
          ["Fixture factor", formatSigned(assistModel.fixture_factor || 0)],
        ])}
      </div>
    </article>
    <article class="detail-card">
      <h3>Defence And Extras</h3>
      <div class="detail-list">
        ${detailRows([
          ["Clean sheet probability / fixture", formatSigned(inputs.clean_sheet_probability_per_fixture || 0)],
          ["Clean sheet points multiplier", formatSigned(inputs.position_clean_sheet_points || 0)],
          ["Defensive contribution / fixture", formatSigned(inputs.defensive_contribution_per_fixture || 0)],
          ["Bonus / fixture", formatSigned(inputs.bonus_per_fixture || 0)],
          ["Yellow cards / fixture", formatSigned(inputs.yellow_cards_per_fixture || 0)],
          ["Goal points multiplier", formatSigned(inputs.position_goal_points || 0)],
        ])}
      </div>
    </article>
  `;
  elements.playerModal.hidden = false;
}

function closePlayerModal() {
  state.activePlayer = null;
  elements.playerModal.hidden = true;
}

function renderTable() {
  state.players = [...getWindowPlayers()].sort(comparePlayers);

  if (state.players.length === 0) {
    elements.resultsBody.innerHTML = `
      <tr>
        <td colspan="10">No prediction rows are available for this gameweek range yet.</td>
      </tr>
    `;
    elements.playerCount.textContent = "0";
    updateOptionalColumns();
    updateSortButtons();
    return;
  }

  const rows = state.players.map((player, index) => {
    const topPickClass = index < 5 ? "top-pick" : "";
    return `
      <tr class="${topPickClass}">
        <td>
          <button class="player-button" type="button" data-player-id="${player.player_id}">
            <strong>${escapeHtml(player.player_name)}</strong>
          </button><br>
          ${escapeHtml(player.team)} · ${escapeHtml(player.position)}
        </td>
        <td><strong>${formatSigned(displayedTotalPoints(player))}</strong></td>
        <td>${formatSigned(player.components.minutes_points)}</td>
        <td>${formatSigned(player.components.goal_points)}</td>
        <td>${formatSigned(player.components.assist_points)}</td>
        <td>${formatSigned(player.components.clean_sheet_points)}</td>
        <td>${formatSigned(player.components.defensive_contribution_points)}</td>
        <td data-cell-optional="bonus">${formatSigned(player.components.bonus_points)}</td>
        <td data-cell-optional="yellow">-${formatSigned(player.components.yellow_cards)}</td>
        <td class="fixture-list">${player.fixtures.map(fixtureLabel).join("<br>")}</td>
      </tr>
    `;
  });

  elements.resultsBody.innerHTML = rows.join("");
  elements.playerCount.textContent = String(state.players.length);
  updateOptionalColumns();
  updateSortButtons();
}

function updateStatusText(prefix = "Showing") {
  const generatedAt = state.dataset?.generated_at
    ? new Date(state.dataset.generated_at).toLocaleString()
    : "unknown time";
  const sourceFetchAt = state.dataset?.source_last_fetch_at
    ? new Date(state.dataset.source_last_fetch_at).toLocaleString()
    : "unknown source fetch time";
  const cacheNote = state.dataset?.used_cached_data ? " Using cached source data." : "";
  const selected = getSelectedGameweeks();
  const sourceLabel = getSourceData()?.label || state.activeSource;
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

    state.dataset = payload;
    state.activeSource = payload.default_source || "official";
    configureRangeControl();
    updateSourceButtons();
    renderTable();
    updateStatusText("Static data updated");
  } catch (error) {
    elements.statusText.textContent = `Static data load failed: ${error.message}`;
    elements.resultsBody.innerHTML = "";
    elements.playerCount.textContent = "0";
  }
}

function refreshView() {
  if (!state.dataset) {
    return;
  }

  renderRangeLabels();
  renderRangeFill();
  updateRangeSummary();
  renderTable();
  updateStatusText("Showing");
}

elements.startGw.addEventListener("input", () => {
  applyStartBounds();
  refreshView();
});

elements.endGw.addEventListener("input", () => {
  applyEndBounds();
  refreshView();
});

elements.showBonus.addEventListener("change", refreshView);
elements.showYellows.addEventListener("change", refreshView);
elements.refreshButton.addEventListener("click", loadPredictions);
elements.positionFilter.addEventListener("change", refreshView);

elements.sortButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const selectedKey = button.dataset.sort;
    if (state.sortKey === selectedKey) {
      state.sortDirection = state.sortDirection === "desc" ? "asc" : "desc";
    } else {
      state.sortKey = selectedKey;
      state.sortDirection = "desc";
    }
    renderTable();
  });
});

elements.sourceButtons.forEach((button) => {
  button.addEventListener("click", () => {
    state.activeSource = button.dataset.source;
    updateSourceButtons();
    refreshView();
  });
});

elements.resultsBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-player-id]");
  if (!button) {
    return;
  }
  openPlayerModal(button.dataset.playerId);
});

elements.closeModalButton.addEventListener("click", closePlayerModal);
elements.playerModal.addEventListener("click", (event) => {
  if (event.target === elements.playerModal) {
    closePlayerModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.playerModal.hidden) {
    closePlayerModal();
  }
});

updateOptionalColumns();
loadPredictions();
