const state = {
  dataset: null,
  players: [],
  availableGameweeks: [],
};

const elements = {
  horizon: document.getElementById("horizon"),
  horizonValue: document.getElementById("horizonValue"),
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
};

function formatSigned(value) {
  return Number(value).toFixed(2);
}

function getSelectedGameweek() {
  const index = Number(elements.horizon.value) - 1;
  return state.availableGameweeks[index] || state.availableGameweeks[0] || null;
}

function getSelectedHorizon() {
  return String(Number(elements.horizon.value));
}

function fixtureLabel(fixture) {
  return `GW${fixture.event}: ${fixture.opponent} (${fixture.home ? "H" : "A"}, diff ${fixture.difficulty})`;
}

function displayedTotalPoints(player) {
  let total = Number(player.predicted_total_points);

  if (elements.showBonus.checked) {
    total -= Number(player.components.bonus_points || 0);
  }
  if (elements.showYellows.checked) {
    total += Number(player.components.yellow_cards || 0);
  }
  if (elements.showPenalty.checked) {
    total += Number(player.components.sub_60_penalty || 0);
  }

  return total;
}

function renderHorizonLabels() {
  elements.horizonLabels.innerHTML = state.availableGameweeks
    .map((gameweek) => `<span>GW${gameweek}</span>`)
    .join("");
}

function updateHorizonDisplay() {
  const selectedGameweek = getSelectedGameweek();
  elements.horizonValue.textContent = selectedGameweek ? `GW ${selectedGameweek}` : "GW -";
}

function configureHorizonControl() {
  const gameweeks = state.dataset?.available_gameweeks || [];
  state.availableGameweeks = gameweeks.length > 0 ? gameweeks : [1];
  elements.horizon.min = "1";
  elements.horizon.max = String(state.availableGameweeks.length);
  if (Number(elements.horizon.value) > state.availableGameweeks.length) {
    elements.horizon.value = String(state.availableGameweeks.length);
  }
  if (Number(elements.horizon.value) < 1) {
    elements.horizon.value = "1";
  }
  renderHorizonLabels();
  updateHorizonDisplay();
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

function getVisiblePlayers() {
  if (!state.dataset) {
    return [];
  }

  const position = elements.positionFilter.value;
  const horizonData = (state.dataset.predictions || {})[getSelectedHorizon()] || [];

  if (position === "ALL") {
    return horizonData;
  }

  return horizonData.filter((player) => player.position === position);
}

function renderTable() {
  state.players = getVisiblePlayers();

  if (state.players.length === 0) {
    elements.resultsBody.innerHTML = `
      <tr>
        <td colspan="11">No prediction rows are available for this horizon yet.</td>
      </tr>
    `;
    elements.playerCount.textContent = "0";
    updateOptionalColumns();
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
    configureHorizonControl();
    renderTable();
    if (payload.generated_at) {
      const generatedAt = new Date(payload.generated_at);
      const sourceFetchAt = payload.source_last_fetch_at
        ? new Date(payload.source_last_fetch_at).toLocaleString()
        : "unknown source fetch time";
      const cacheNote = payload.used_cached_data ? " Using cached source data." : "";
      const selectedGameweek = getSelectedGameweek();
      elements.statusText.textContent = `Static data updated ${generatedAt.toLocaleString()}. Viewing through GW${selectedGameweek}. Source fetch: ${sourceFetchAt}.${cacheNote}`;
    } else {
      elements.statusText.textContent = "Static data loaded.";
    }
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

  renderTable();
  updateHorizonDisplay();
  const generatedAt = state.dataset.generated_at
    ? new Date(state.dataset.generated_at).toLocaleString()
    : "unknown time";
  const sourceFetchAt = state.dataset.source_last_fetch_at
    ? new Date(state.dataset.source_last_fetch_at).toLocaleString()
    : "unknown source fetch time";
  const cacheNote = state.dataset.used_cached_data ? " Using cached source data." : "";
  const selectedGameweek = getSelectedGameweek();
  elements.statusText.textContent = `Showing predictions through GW${selectedGameweek} from ${generatedAt}. Source fetch: ${sourceFetchAt}.${cacheNote}`;
}

elements.horizon.addEventListener("input", () => {
  updateHorizonDisplay();
});

elements.showBonus.addEventListener("change", refreshView);
elements.showYellows.addEventListener("change", refreshView);
elements.showPenalty.addEventListener("change", refreshView);
elements.refreshButton.addEventListener("click", loadPredictions);
elements.positionFilter.addEventListener("change", refreshView);
elements.horizon.addEventListener("change", refreshView);

updateOptionalColumns();
loadPredictions();
