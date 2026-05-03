const state = {
  dataset: null,
  players: [],
};

const elements = {
  horizon: document.getElementById("horizon"),
  horizonValue: document.getElementById("horizonValue"),
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

function fixtureLabel(fixture) {
  return `GW${fixture.event}: ${fixture.opponent} (${fixture.home ? "H" : "A"}, diff ${fixture.difficulty})`;
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

  const horizon = elements.horizon.value;
  const position = elements.positionFilter.value;
  const horizonData = (state.dataset.predictions || {})[horizon] || [];

  if (position === "ALL") {
    return horizonData;
  }

  return horizonData.filter((player) => player.position === position);
}

function renderTable() {
  state.players = getVisiblePlayers();

  const rows = state.players.map((player, index) => {
    const topPickClass = index < 5 ? "top-pick" : "";
    return `
      <tr class="${topPickClass}">
        <td>
          <strong>${player.player_name}</strong><br>
          ${player.team} · ${player.position}
        </td>
        <td><strong>${formatSigned(player.predicted_total_points)}</strong></td>
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
    renderTable();
    if (payload.generated_at) {
      const generatedAt = new Date(payload.generated_at);
      elements.statusText.textContent = `Static data updated ${generatedAt.toLocaleString()}.`;
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
  const generatedAt = state.dataset.generated_at
    ? new Date(state.dataset.generated_at).toLocaleString()
    : "unknown time";
  elements.statusText.textContent = `Showing ${elements.horizon.value} GW predictions from ${generatedAt}.`;
}

elements.horizon.addEventListener("input", () => {
  elements.horizonValue.textContent = elements.horizon.value;
});

elements.showBonus.addEventListener("change", updateOptionalColumns);
elements.showYellows.addEventListener("change", updateOptionalColumns);
elements.showPenalty.addEventListener("change", updateOptionalColumns);
elements.refreshButton.addEventListener("click", loadPredictions);
elements.positionFilter.addEventListener("change", refreshView);
elements.horizon.addEventListener("change", refreshView);

updateOptionalColumns();
loadPredictions();
