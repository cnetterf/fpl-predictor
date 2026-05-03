const state = {
  dataset: null,
  players: [],
  availableGameweeks: [],
  sortKey: "total",
  sortDirection: "desc",
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
  showPenalty: document.getElementById("showPenalty"),
  refreshButton: document.getElementById("refreshButton"),
  playerCount: document.getElementById("playerCount"),
  statusText: document.getElementById("statusText"),
  resultsBody: document.getElementById("resultsBody"),
  optionalHeaders: document.querySelectorAll("[data-optional]"),
  sortButtons: document.querySelectorAll("[data-sort]"),
};

function formatSigned(value) {
  return Number(value).toFixed(2);
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
  if (!elements.showPenalty.checked) {
    total += Number(player.components.sub_60_penalty || 0);
  }

  return total;
}

function updateOptionalColumns() {
  const mapping = {
    bonus: elements.showBonus.checked,
    yellow: elements.showYellows.checked,
    penalty: elements.showPenalty.checked,
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
  let startIndex = getStartIndex();
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
  let endIndex = getEndIndex();

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

function getWindowPlayers() {
  if (!state.dataset || state.availableGameweeks.length === 0) {
    return [];
  }

  const selected = getSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    return [];
  }

  const startBucket = (state.dataset.predictions || {})[String(selected.start)] || {};
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
    total: Number(player.predicted_total_points),
    minutes: Number(components.minutes_points),
    goal: Number(components.goal_points),
    assist: Number(components.assist_points),
    clean_sheet: Number(components.clean_sheet_points),
    defensive: Number(components.defensive_contribution_points),
    bonus: Number(components.bonus_points),
    yellow: Number(components.yellow_cards),
    penalty: Number(components.sub_60_penalty),
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

function renderTable() {
  state.players = [...getWindowPlayers()].sort(comparePlayers);

  if (state.players.length === 0) {
    elements.resultsBody.innerHTML = `
      <tr>
        <td colspan="11">No prediction rows are available for this gameweek range yet.</td>
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
          <strong>${player.player_name}</strong><br>
          ${player.team} · ${player.position}
        </td>
        <td><strong>${formatSigned(displayedTotalPoints(player))}</strong></td>
        <td>${formatSigned(player.components.minutes_points)}</td>
        <td>${formatSigned(player.components.goal_points)}</td>
        <td>${formatSigned(player.components.assist_points)}</td>
        <td>${formatSigned(player.components.clean_sheet_points)}</td>
        <td>${formatSigned(player.components.defensive_contribution_points)}</td>
        <td data-cell-optional="bonus">${formatSigned(player.components.bonus_points)}</td>
        <td data-cell-optional="yellow">-${formatSigned(player.components.yellow_cards)}</td>
        <td data-cell-optional="penalty">-${formatSigned(player.components.sub_60_penalty)}</td>
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
  elements.statusText.textContent = `${prefix} predictions from GW${selected.start} to GW${selected.end} from ${generatedAt}. Source fetch: ${sourceFetchAt}.${cacheNote}`;
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
    configureRangeControl();
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
elements.showPenalty.addEventListener("change", refreshView);
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

updateOptionalColumns();
loadPredictions();
