const EXCLUDED_PLAYERS_STORAGE_KEY = "fpl-predictor-excluded-player-ids-v1";

function loadExcludedPlayerIds() {
  try {
    const storedIds = JSON.parse(window.localStorage.getItem(EXCLUDED_PLAYERS_STORAGE_KEY) || "[]");
    return new Set(Array.isArray(storedIds) ? storedIds.map(String) : []);
  } catch (error) {
    return new Set();
  }
}

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
    windowCache: {},
    windowPromises: {},
    refreshToken: 0,
    excludedPlayerIds: loadExcludedPlayerIds(),
    showExcludedPlayers: false,
  },
  backtest: {
    dataset: null,
    seasons: [],
    activeSeason: null,
    availableGameweeks: [],
    allTeams: [],
    selectedTeams: new Set(),
    teamsInitialized: false,
    playerQuery: "",
    positionFilter: "ALL",
    selectedPlayerId: null,
    sortKey: "official_error",
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
  showExcludedPlayersButton: document.getElementById("showExcludedPlayersButton"),
  statusText: document.getElementById("statusText"),
  resultsBody: document.getElementById("resultsBody"),
  optionalHeaders: document.querySelectorAll("[data-optional]"),
  sortButtons: document.querySelectorAll("[data-sort]"),

  backtestStartGw: document.getElementById("backtestStartGw"),
  backtestSeasonSelect: document.getElementById("backtestSeasonSelect"),
  backtestEndGw: document.getElementById("backtestEndGw"),
  backtestRangeValue: document.getElementById("backtestRangeValue"),
  backtestRangeSpan: document.getElementById("backtestRangeSpan"),
  backtestRangeFill: document.getElementById("backtestRangeFill"),
  backtestRangeLabels: document.getElementById("backtestRangeLabels"),
  backtestHorizonInput: document.getElementById("backtestHorizonInput"),
  backtestPositionFilter: document.getElementById("backtestPositionFilter"),
  backtestPlayerSearch: document.getElementById("backtestPlayerSearch"),
  backtestPlayerSelect: document.getElementById("backtestPlayerSelect"),
  backtestTeamFilterList: document.getElementById("backtestTeamFilterList"),
  backtestSelectAllTeamsButton: document.getElementById("backtestSelectAllTeamsButton"),
  backtestClearAllTeamsButton: document.getElementById("backtestClearAllTeamsButton"),
  backtestSummaryCards: document.getElementById("backtestSummaryCards"),
  backtestExplorerBody: document.getElementById("backtestExplorerBody"),
  backtestTrendChart: document.getElementById("backtestTrendChart"),
  backtestSpanChart: document.getElementById("backtestSpanChart"),
  backtestVarianceGrid: document.getElementById("backtestVarianceGrid"),
  backtestVarianceNote: document.getElementById("backtestVarianceNote"),
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

function saveExcludedPlayerIds() {
  try {
    window.localStorage.setItem(
      EXCLUDED_PLAYERS_STORAGE_KEY,
      JSON.stringify([...state.predictor.excludedPlayerIds]),
    );
    return true;
  } catch (error) {
    return false;
  }
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

let glossaryTooltipCounter = 0;

function detailRows(rows) {
  return rows.map((row) => {
    const config = Array.isArray(row)
      ? { label: row[0], value: row[1], emphasis: true }
      : row;
    const classes = ["detail-row", config.className || ""].filter(Boolean).join(" ");
    const helpLines = (config.help || []).filter(Boolean);
    let labelMarkup = `<span class="detail-label">${escapeHtml(config.label)}</span>`;
    if (helpLines.length > 0) {
      const tooltipId = `glossary-tooltip-${++glossaryTooltipCounter}`;
      labelMarkup = `
        <span class="glossary-anchor">
          <button class="glossary-trigger" type="button" aria-expanded="false" aria-controls="${tooltipId}">
            <span>${escapeHtml(config.label)}</span><span class="glossary-symbol" aria-hidden="true">?</span>
          </button>
          <span class="glossary-tooltip" id="${tooltipId}" role="tooltip">
            ${helpLines.map((line) => `<span>${escapeHtml(line)}</span>`).join("")}
          </span>
        </span>
      `;
    }
    const valueTag = config.emphasis === false ? "span" : "strong";
    return `<div class="${escapeHtml(classes)}">${labelMarkup}<${valueTag} class="detail-value">${escapeHtml(config.value)}</${valueTag}></div>`;
  }).join("");
}

function sampleMatchLabel(match) {
  return `${match.prior_season ? "2025-26 " : ""}GW${match.round}`;
}

function sampleGamesLine(matches) {
  if (!matches.length) {
    return "Games: no completed matches were available in the sample.";
  }
  return `Games: ${matches.map((match) => (
    `${sampleMatchLabel(match)} (${Number(match.minutes || 0)} mins, ${Number(match.starts) > 0 ? "started" : Number(match.minutes) > 0 ? "sub" : "did not appear"})`
  )).join("; ")}.`;
}

function sampleStatLine(matches, field, label, digits = 3) {
  if (!matches.length || matches.every((match) => match[field] === undefined)) {
    return "";
  }
  return `${label}: ${matches.map((match) => (
    `${sampleMatchLabel(match)} ${formatNumber(match[field] || 0, digits)}`
  )).join(" + ")}.`;
}

function fixtureFactorLines(fixtures) {
  const mapped = fixtures.map((fixture) => {
    const model = fixture.fixture_model || {};
    if (model.method !== "elo") {
      return `${fixtureLabel(fixture)} → leakage-safe historical team-strength fallback ${formatNumber(fixture.combined_attack_factor, 3)}`;
    }
    return `${fixtureLabel(fixture)} → raw Elo ${formatNumber(model.elo_home_raw, 0)}–${formatNumber(model.elo_away_raw, 0)}; home +100 gives ΔElo ${formatSigned(model.elo_delta, 0)}; 1 + 0.55 × tanh(${formatNumber(model.elo_delta, 0)} ÷ 400), viewed for this team = ${formatNumber(fixture.combined_attack_factor, 3)}; team xG ${formatNumber(model.team_xg, 3)}, opponent xG ${formatNumber(model.opponent_xg, 3)}`;
  });
  return mapped.length ? [`Upcoming fixtures: ${mapped.join("; ")}.`] : ["Upcoming fixtures: none."];
}

function fixtureLabel(fixture) {
  return `GW${fixture.event}: ${fixture.opponent} (${fixture.home ? "H" : "A"})`;
}

function fixtureFactorColor(rawFactor) {
  const factor = Math.min(1.55, Math.max(0.45, Number(rawFactor) || 1));
  const endpoint = factor >= 1 ? [79, 184, 107] : [214, 83, 93];
  const amount = factor >= 1 ? (factor - 1) / 0.55 : (1 - factor) / 0.55;
  const rgb = endpoint.map((channel) => Math.round(255 + (channel - 255) * amount));
  return `rgb(${rgb.join(",")})`;
}

function splitPlayerName(playerName) {
  const parts = String(playerName || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length < 2) {
    return { givenName: "", surname: parts[0] || "" };
  }

  const surnameParticles = new Set(["da", "de", "del", "di", "dos", "la", "le", "van", "von"]);
  let surnameStart = parts.length - 1;
  while (surnameStart > 0 && surnameParticles.has(parts[surnameStart - 1].toLowerCase())) {
    surnameStart -= 1;
  }

  return {
    givenName: parts.slice(0, surnameStart).join(" "),
    surname: parts.slice(surnameStart).join(" "),
  };
}

function fixtureTilesMarkup(fixtures) {
  return `<div class="fixture-tiles">${fixtures.map((fixture) => {
    const opponent = String(fixture.opponent || "");
    const displayOpponent = fixture.home ? opponent.toUpperCase() : opponent.toLowerCase();
    const hasPredictedPoints = fixture.predicted_points !== undefined && fixture.predicted_points !== null;
    const predictedPoints = hasPredictedPoints ? displayedFixturePoints(fixture) : null;
    const label = hasPredictedPoints
      ? `${fixtureLabel(fixture)}, ${formatNumber(predictedPoints)} predicted points`
      : fixtureLabel(fixture);
    return `
      <span class="fixture-item" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">
        <span class="fixture-tile" style="background:${fixtureFactorColor(fixture.combined_attack_factor)}">
          <span class="fixture-opponent">${escapeHtml(displayOpponent)}</span>
          <span class="fixture-gameweek">GW${escapeHtml(fixture.event)}</span>
        </span>
        ${hasPredictedPoints ? `<span class="fixture-points">${formatNumber(predictedPoints)}</span>` : ""}
      </span>
    `;
  }).join("")}</div>`;
}

function sourceDetailMarkup(label, player) {
  const inputs = player.inputs || {};
  const components = player.components || {};
  const goalModel = inputs.goal_model || {};
  const assistModel = inputs.assist_model || {};
  const cleanSheetModel = inputs.clean_sheet_model || {};
  const defensiveModel = inputs.defensive_contribution_model || {};
  const goalkeeperModel = inputs.goalkeeper_model || {};
  const bonusModel = inputs.bonus_model || {};
  const yellowModel = inputs.yellow_card_model || {};
  const matches = inputs.minutes_sample || [];
  const sampleSize = matches.length;
  const sourceHistory = `${label} player match history`;
  const gamesLine = sampleGamesLine(matches);
  const fixtureCount = Number(player.horizon || player.fixtures?.length || 0);
  const recentMinutes = matches.reduce((sum, match) => sum + Number(match.minutes || 0), 0);
  const starts = matches.filter((match) => Number(match.starts) > 0);
  const subAppearances = matches.filter((match) => Number(match.starts) === 0 && Number(match.minutes) > 0);
  const startMinutes = starts.reduce((sum, match) => sum + Number(match.minutes || 0), 0);
  const substituteMinutes = subAppearances.reduce((sum, match) => sum + Number(match.minutes || 0), 0);
  const predictedMinutes = Number(inputs.predicted_minutes_per_fixture || 0);
  const fullMinutesThreshold = Number(inputs.minutes_points_full_threshold || 80);
  const modelTotal = Number(player.predicted_total_points || 0);
  const displayedTotal = displayedTotalPoints(player);
  const componentSum = (
    Number(components.minutes_points || 0)
    + Number(components.goal_points || 0)
    + Number(components.assist_points || 0)
    + Number(components.clean_sheet_points || 0)
    + Number(components.defensive_contribution_points || 0)
    + Number(components.bonus_points || 0)
    + Number(components.save_points || 0)
    - Number(components.goals_conceded_deduction || 0)
    - Number(components.yellow_cards || 0)
  );
  const rawReconciliationError = componentSum - modelTotal;
  const reconciliationError = Math.abs(rawReconciliationError) < 1e-9 ? 0 : rawReconciliationError;
  const displayAdjustments = [];
  if (!elements.showBonus.checked) {
    displayAdjustments.push(`minus hidden bonus points ${formatNumber(components.bonus_points)}`);
  }
  if (!elements.showYellows.checked) {
    displayAdjustments.push(`plus hidden yellow-card deduction ${formatNumber(components.yellow_cards)}`);
  }
  if (displayAdjustments.length === 0) {
    displayAdjustments.push("no display adjustments");
  }
  const cleanSheetFixtureLines = (cleanSheetModel.fixtures || []).map((fixture) => (
    fixture.method === "elo"
      ? `GW${fixture.event} ${fixture.opponent} (${fixture.home ? "H" : "A"}): opponent xG ${formatNumber(fixture.opponent_xg, 3)} gives team xCS e^−${formatNumber(fixture.opponent_xg, 3)} = ${formatNumber((fixture.team_probability || 0) * 100, 1)}%; × P(60+) ${formatNumber((fixture.probability_reaches_60 || 0) * 100, 1)}% = player CS probability ${formatNumber((fixture.probability || 0) * 100, 1)}%`
      : `GW${fixture.event} ${fixture.opponent} (${fixture.home ? "H" : "A"}): historical strength fallback gives team xCS ${formatNumber((fixture.team_probability || 0) * 100, 1)}%; × P(60+) ${formatNumber((fixture.probability_reaches_60 || 0) * 100, 1)}% = ${formatNumber((fixture.probability || 0) * 100, 1)}%`
  ));
  const goalFactorHelp = fixtureFactorLines(player.fixtures || []);
  const goalPriorApplied = Boolean(goalModel.used_team_position_prior || goalModel.used_team_position_fallback);
  const longTermGoalBlendLine = `Player rate blend: ${formatNumber((goalModel.long_term_weight || 0) * 100, 0)}% long-term (${formatNumber(goalModel.long_term_xg_total, 3)} xG over ${formatNumber(goalModel.long_term_minutes_total, 0)} minutes = ${formatNumber(goalModel.long_term_xg_per_90, 3)} xG/90) + ${formatNumber((goalModel.recent_weight || 0) * 100, 0)}% latest-six rate = ${formatNumber(goalModel.xg_per_90, 3)} xG/90.`;
  const goalRateHelp = goalModel.used_team_position_prior ? [
    `Short-history adjustment: ${goalModel.missing_sample_fixtures || 0} missing fixture slots are represented by ${formatNumber(goalModel.prior_equivalent_minutes, 0)} minutes at the ${label} same-team/same-position prior of ${formatNumber(goalModel.prior_xg_per_90, 3)} xG/90; the league position average is the fallback.`,
    longTermGoalBlendLine,
  ] : goalModel.used_team_position_fallback ? [
    `No player minutes were available, so xG/90 uses the ${label} same-team/same-position prior of ${formatNumber(goalModel.prior_xg_per_90, 3)}; the league position average is the fallback.`,
  ] : [
    longTermGoalBlendLine,
  ];
  const assistRateHelp = assistModel.used_team_position_prior ? [
    `Short-history adjustment: ${assistModel.missing_sample_fixtures || 0} missing fixture slots are represented by ${formatNumber(assistModel.prior_equivalent_minutes, 0)} minutes at the ${label} same-team/same-position prior of ${formatNumber(assistModel.prior_xa_per_90, 3)} xA/90; the league position average is the fallback.`,
    `Player rate blend: ${formatNumber((assistModel.long_term_weight || 0) * 100, 0)}% long-term (${formatNumber(assistModel.long_term_xa_per_90, 3)} xA/90) + ${formatNumber((assistModel.recent_weight || 0) * 100, 0)}% latest-six = ${formatNumber(assistModel.xa_per_90, 3)} xA/90.`,
  ] : assistModel.used_team_position_fallback ? [
    `No player minutes were available, so xA/90 uses the ${label} same-team/same-position prior of ${formatNumber(assistModel.prior_xa_per_90, 3)}; the league position average is the fallback.`,
  ] : [
    `Player rate blend: ${formatNumber((assistModel.long_term_weight || 0) * 100, 0)}% long-term (${formatNumber(assistModel.long_term_xa_per_90, 3)} xA/90) + ${formatNumber((assistModel.recent_weight || 0) * 100, 0)}% latest-six = ${formatNumber(assistModel.xa_per_90, 3)} xA/90.`,
  ];
  const teamXgAuditLines = (goalModel.team_xg_audit || []).map((audit) => (
    `GW${audit.event} ${audit.opponent} (${audit.home ? "H" : "A"}): summed player forecast ${formatNumber(audit.summed_player_xg, 3)} versus Elo team xG ${formatNumber(audit.team_xg, 3)} (${formatNumber((audit.ratio || 0) * 100, 1)}%)${audit.warning ? " — warning: above 115%" : ""}.`
  ));

  return `
    <section class="stack">
      <div class="source-kicker">${escapeHtml(label)}</div>
      <article class="detail-card">
        <h3>Total</h3>
        <div class="metric-list">
          ${detailRows([
            {
              label: "Displayed total",
              value: formatNumber(displayedTotal),
              className: "detail-row-displayed",
              help: [
                "Source: the stored model total adjusted only by the Bonus and Yellows display switches.",
                `Calculation: ${formatNumber(modelTotal)}; ${displayAdjustments.join("; ")} = ${formatNumber(displayedTotal)}.`,
              ],
            },
            {
              label: "Minutes points",
              value: formatNumber(components.minutes_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                `Source: expected minutes from ${sourceHistory}.`,
                `Calculation: ${formatNumber(inputs.minutes_points_per_fixture)} per fixture × ${fixtureCount} fixtures = ${formatNumber(components.minutes_points)}. See Minutes for the sample and threshold calculation.`,
              ],
            },
            {
              label: "Goal points",
              value: formatNumber(components.goal_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                `Source: predicted goals from ${sourceHistory}.`,
                `Calculation: ${formatNumber(inputs.goals_per_fixture, 3)} goals per fixture × ${fixtureCount} fixtures × ${formatNumber(inputs.position_goal_points, 0)} position points = ${formatNumber(components.goal_points)}.`,
              ],
            },
            {
              label: "Assist points",
              value: formatNumber(components.assist_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                `Source: predicted assists from ${sourceHistory}.`,
                `Calculation: ${formatNumber(inputs.assists_per_fixture, 3)} assists per fixture × ${fixtureCount} fixtures × 3 points = ${formatNumber(components.assist_points)}.`,
              ],
            },
            {
              label: "Clean sheet points",
              value: formatNumber(components.clean_sheet_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                "Source: current validated team Elo ratings, converted to opponent xG and Poisson team clean-sheet probability for each fixture.",
                `Calculation: mean player CS probability ${formatNumber(inputs.clean_sheet_probability_per_fixture, 3)} × ${fixtureCount} fixtures × ${formatNumber(inputs.position_clean_sheet_points, 0)} position points. Each player probability also includes P(60+) ${formatNumber((inputs.probability_reaches_60 || 0) * 100, 1)}%.`,
              ],
            },
            {
              label: "Defensive points",
              value: formatNumber(components.defensive_contribution_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                defensiveModel.method === "ineligible_position"
                  ? "Goalkeepers are not eligible for defensive-contribution points, so this component is zero."
                  : `FPL awards two points when the player reaches ${defensiveModel.threshold} defensive contributions in a fixture. The player reached that threshold in ${defensiveModel.qualifying_fixtures || 0} of ${defensiveModel.sample_size || 0} sampled team fixtures.`,
                `Calculation: ${formatNumber(inputs.defensive_contribution_per_fixture, 3)} points/fixture × ${fixtureCount} = ${formatNumber(components.defensive_contribution_points)}.`,
              ],
            },
            {
              label: "Save points",
              value: formatNumber(components.save_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                goalkeeperModel.save_method === "historical_save_points_per_90"
                  ? `Proxy: ${formatNumber(goalkeeperModel.save_points_sample_total, 0)} historical save points over ${formatNumber(goalkeeperModel.save_minutes_sample_total, 0)} sampled minutes = ${formatNumber(goalkeeperModel.save_points_per_90, 3)} per 90.`
                  : "Only goalkeepers receive save points, so this component is zero.",
                `Expected save points: ${formatNumber(goalkeeperModel.save_points_per_fixture, 3)} per fixture × ${fixtureCount} = ${formatNumber(components.save_points)}.`,
              ],
            },
            {
              label: "Goals-conceded deduction",
              value: `-${formatNumber(components.goals_conceded_deduction)}`,
              emphasis: false,
              className: "detail-row-component",
              help: [
                "Goalkeepers and defenders lose one point for each complete pair of goals conceded. Fixture xGA is treated as a Poisson mean after expected-minutes scaling.",
                ...((goalkeeperModel.fixtures || []).map((fixture) => `GW${fixture.event} ${fixture.opponent}: opponent xG ${formatNumber(fixture.opponent_xg, 3)} × xMins/90 = ${formatNumber(fixture.expected_goals_conceded, 3)} expected goals conceded; expected deduction ${formatNumber(fixture.expected_deduction, 3)}.`)),
              ],
            },
            {
              label: "Bonus points",
              value: formatNumber(components.bonus_points),
              emphasis: false,
              className: "detail-row-component",
              help: [
                `Source: player bonus history from ${sourceHistory}, converted using expected minutes; no goal, assist or defensive lift is added.`,
                `Calculation: ${formatNumber(inputs.bonus_per_fixture, 3)} per fixture × ${fixtureCount} fixtures = ${formatNumber(components.bonus_points)}. See Bonus / fixture for the complete player-rate calculation.`,
              ],
            },
            {
              label: "Yellow-card deduction",
              value: formatNumber(components.yellow_cards),
              emphasis: false,
              className: "detail-row-component",
              help: [
                `Source: yellow cards in the ${sourceHistory} sample.`,
                `Calculation: ${formatNumber(inputs.yellow_cards_per_fixture, 3)} per fixture × ${fixtureCount} fixtures = ${formatNumber(components.yellow_cards)} points subtracted.`,
              ],
            },
            {
              label: "Model total",
              value: formatNumber(modelTotal),
              className: "detail-row-model",
              help: [
                "Source: the model sums the unrounded component predictions, subtracts yellow cards, then rounds the final result.",
                `Displayed component calculation: ${formatNumber(components.minutes_points)} + ${formatNumber(components.goal_points)} + ${formatNumber(components.assist_points)} + ${formatNumber(components.clean_sheet_points)} + ${formatNumber(components.defensive_contribution_points)} + ${formatNumber(components.bonus_points)} + ${formatNumber(components.save_points)} − ${formatNumber(components.goals_conceded_deduction)} − ${formatNumber(components.yellow_cards)} = ${formatNumber(componentSum)}.`,
              ],
            },
            {
              label: "Component sum − model total",
              value: formatSigned(reconciliationError),
              emphasis: false,
              className: "detail-row-reconciliation",
              help: [
                "This reconciliation exposes differences caused by summing the individually rounded displayed components rather than the model’s unrounded inputs.",
                `Calculation: ${formatNumber(componentSum)} − ${formatNumber(modelTotal)} = ${formatSigned(reconciliationError)}.`,
              ],
            },
          ])}
        </div>
      </article>
      <article class="detail-card">
        <h3>Minutes</h3>
        <div class="metric-list">
          ${detailRows([
            {
              label: "Predicted minutes / fixture",
              value: formatNumber(predictedMinutes),
              help: [
                `Source: ${sourceHistory}; zero-minute non-appearances remain in the six-fixture sample.`,
                gamesLine,
                `Calculation: P(start) ${formatNumber((inputs.start_probability || 0) * 100, 1)}% × ${formatNumber(inputs.minutes_if_starting)} + P(sub appearance) ${formatNumber((inputs.sub_appearance_probability || 0) * 100, 1)}% × ${formatNumber(inputs.minutes_if_substitute)} = ${formatNumber(predictedMinutes)}.`,
              ],
            },
            {
              label: "Minutes points / fixture",
              value: formatNumber(inputs.minutes_points_per_fixture),
              help: [
                "Source: predicted minutes calculated directly above.",
                predictedMinutes >= fullMinutesThreshold
                  ? `Calculation: ${formatNumber(predictedMinutes)} expected minutes is at least the ${fullMinutesThreshold}-minute full-score threshold, so 2.00 points.`
                  : `Calculation: 2 × ${formatNumber(predictedMinutes)} ÷ 90 = ${formatNumber(inputs.minutes_points_per_fixture)} points. Full points begin at ${fullMinutesThreshold} expected minutes.`,
              ],
            },
            {
              label: "Start probability",
              value: `${formatNumber((inputs.start_probability || 0) * 100, 1)}%`,
              help: [
                `Source: starts recorded in ${sourceHistory}.`,
                gamesLine,
                `Calculation: ${starts.length} starts ÷ ${sampleSize || 1} sampled team fixtures = ${formatNumber((inputs.start_probability || 0) * 100, 1)}%.`,
              ],
            },
            {
              label: "Minutes if starting",
              value: formatNumber(inputs.minutes_if_starting),
              help: [
                `Source: minutes in the ${starts.length} sampled games marked as starts by ${label}.`,
                `Calculation: ${startMinutes} starting minutes ÷ ${starts.length || 1} starts = ${formatNumber(inputs.minutes_if_starting)}.`,
              ],
            },
            {
              label: "Sub appearance probability",
              value: `${formatNumber((inputs.sub_appearance_probability || 0) * 100, 1)}%`,
              help: [
                `Source: non-start appearances with more than zero minutes in ${sourceHistory}.`,
                gamesLine,
                `Calculation: ${subAppearances.length} substitute appearances ÷ ${sampleSize || 1} sampled team fixtures = ${formatNumber((inputs.sub_appearance_probability || 0) * 100, 1)}%.`,
              ],
            },
            {
              label: "Minutes if substitute",
              value: formatNumber(inputs.minutes_if_substitute),
              help: [
                `Source: minutes in the ${subAppearances.length} sampled substitute appearances from ${label}.`,
                `Calculation: ${substituteMinutes} substitute minutes ÷ ${subAppearances.length || 1} appearances = ${formatNumber(inputs.minutes_if_substitute)}.`,
              ],
            },
          ])}
        </div>
      </article>
      <article class="detail-card">
        <h3>Goals</h3>
        <div class="metric-list">
          ${detailRows([
            {
              label: "Predicted goals / fixture",
              value: formatNumber(inputs.goals_per_fixture, 3),
              help: [
                `Source: xG, goals and minutes from ${sourceHistory}, plus the current validated team Elo snapshot for each upcoming fixture.`,
                gamesLine,
                ...goalRateHelp,
                `Calculation: baseline ${formatNumber(goalModel.baseline_per_fixture, 3)} × finishing adjustment ${formatNumber(goalModel.finishing_adjustment, 3)} × fixture factor ${formatNumber(goalModel.fixture_factor, 3)} = ${formatNumber(inputs.goals_per_fixture, 3)}.`,
              ],
            },
            {
              label: "xG / 90",
              value: formatNumber(goalModel.xg_per_90, 3),
              help: [
                `Source: expected goals and minutes from ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "expected_goals", "xG buildup", 3),
                ...goalRateHelp,
              ],
            },
            {
              label: "Recent xG total",
              value: formatNumber(goalModel.recent_xg_total, 3),
              help: [
                `Source: expected goals from ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "expected_goals", "Calculation", 3),
                `Total: ${formatNumber(goalModel.recent_xg_total, 3)} xG.`,
              ],
            },
            {
              label: "Recent goals total",
              value: formatNumber(goalModel.recent_goals_total, 3),
              help: [
                `Source: goals scored in ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "goals_scored", "Calculation", 0),
                `Total: ${formatNumber(goalModel.recent_goals_total, 0)} goals.`,
              ],
            },
            {
              label: "Used team-position prior",
              value: goalPriorApplied ? "Yes" : "No",
              help: [
                "The prior fills missing slots in the six-fixture sample. It supplies the full rate only when no observed minutes are available and disappears once the sample is complete.",
                goalPriorApplied
                  ? `Yes: observed evidence has ${formatNumber((goalModel.observed_weight || 0) * 100, 1)}% weight and the source-specific team-position prior has ${formatNumber((goalModel.prior_weight || 0) * 100, 1)}% weight.`
                  : `No: xG/90 came directly from ${formatNumber(goalModel.recent_xg_total, 3)} xG over ${recentMinutes} sampled minutes.`,
              ],
            },
            {
              label: "Baseline / fixture",
              value: formatNumber(goalModel.baseline_per_fixture, 3),
              help: [
                "This converts the player’s xG/90 rate to the expected playing time before finishing and fixture adjustments.",
                `Calculation: ${formatNumber(goalModel.xg_per_90, 3)} × ${formatNumber(predictedMinutes)} ÷ 90 = ${formatNumber(goalModel.baseline_per_fixture, 3)}.`,
              ],
            },
            {
              label: "Finishing adjustment",
              value: formatNumber(goalModel.finishing_adjustment, 3),
              help: [
                `Source: goals and xG from ${sourceHistory}.`,
                `Latest-six conversion adjustment: ${formatNumber(goalModel.recent_finishing_adjustment, 3)}. Long-term player conversion adjustment: ${formatNumber(goalModel.long_term_finishing_adjustment, 3)}. Each is bounded ${formatNumber(goalModel.finishing_adjustment_min, 2)}–${formatNumber(goalModel.finishing_adjustment_max, 2)}.`,
                `Blend: 75% long-term + 25% latest six = ${formatNumber(goalModel.raw_finishing_adjustment, 3)}; evidence confidence ${formatNumber((goalModel.finishing_confidence || 0) * 100, 1)}% gives final adjustment ${formatNumber(goalModel.finishing_adjustment, 3)}.`,
              ],
            },
            {
              label: "Team xG audit",
              value: goalModel.team_xg_warning ? "Warning" : "Within flag threshold",
              help: [
                "This is an audit flag only: individual player forecasts are not capped or renormalised to the Elo team-xG budget.",
                ...(teamXgAuditLines.length ? teamXgAuditLines : ["No team-xG comparison was available."]),
              ],
            },
            {
              label: "Fixture factor",
              value: formatNumber(goalModel.fixture_factor, 3),
              help: [
                "Source: current team Elo ratings. The home team receives +100 Elo before the continuous tanh fixture factor is calculated; Official FPL difficulty is not used.",
                ...goalFactorHelp,
                `Calculation: mean attack factor across ${fixtureCount} fixtures = ${formatNumber(goalModel.fixture_factor, 3)}.`,
              ],
            },
          ])}
        </div>
      </article>
      <article class="detail-card">
        <h3>Assists And Extras</h3>
        <div class="metric-list">
          ${detailRows([
            {
              label: "Predicted assists / fixture",
              value: formatNumber(inputs.assists_per_fixture, 3),
              help: [
                `Source: xA and assists from ${sourceHistory}, plus the current validated team Elo snapshot for each upcoming fixture.`,
                gamesLine,
                ...assistRateHelp,
                `Calculation: baseline ${formatNumber(assistModel.baseline_per_fixture, 3)} × conversion adjustment ${formatNumber(assistModel.conversion_adjustment, 3)} × fixture factor ${formatNumber(assistModel.fixture_factor, 3)} = ${formatNumber(inputs.assists_per_fixture, 3)}.`,
              ],
            },
            {
              label: "Recent xA total",
              value: formatNumber(assistModel.recent_xa_total, 3),
              help: [
                `Source: expected assists from ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "expected_assists", "Calculation", 3),
                ...assistRateHelp,
                `Expected-minutes baseline: ${formatNumber(assistModel.xa_per_90, 3)} × ${formatNumber(predictedMinutes)} ÷ 90 = ${formatNumber(assistModel.baseline_per_fixture, 3)} xA per fixture.`,
              ],
            },
            {
              label: "Recent assists total",
              value: formatNumber(assistModel.recent_assists_total, 3),
              help: [
                `Source: assists in ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "assists", "Calculation", 0),
                `Raw conversion: (${formatNumber(assistModel.recent_assists_total, 3)} + 1) ÷ (${formatNumber(assistModel.recent_xa_total, 3)} + 1), bounded 0.700–1.200 = ${formatNumber(assistModel.raw_conversion_adjustment, 3)}.`,
                `Confidence adjustment: 1 + ${formatNumber((assistModel.observed_weight || 0) * 100, 1)}% × (${formatNumber(assistModel.raw_conversion_adjustment, 3)} − 1) = ${formatNumber(assistModel.conversion_adjustment, 3)}.`,
              ],
            },
            {
              label: "CS probability / fixture",
              value: formatNumber(inputs.clean_sheet_probability_per_fixture, 3),
              help: [
                "Source: current team Elo ratings. Each match produces opponent xG; Poisson P(opponent scores zero) gives team xCS, then the player's empirical P(60+) is applied.",
                ...(cleanSheetFixtureLines.length ? cleanSheetFixtureLines : ["No upcoming fixture calculation was available."]),
                `Calculation: mean of the player-specific fixture probabilities = ${formatNumber(inputs.clean_sheet_probability_per_fixture, 3)}.`,
              ],
            },
            {
              label: "Bonus / fixture",
              value: formatNumber(inputs.bonus_per_fixture, 3),
              help: bonusModel.method === "early_six_fixture_position_fill" ? [
                `Source: through GW6, player bonus and minutes from ${sourceHistory}. The previous-season ${bonusModel.position || player.position} average fills only genuinely missing slots in the six-fixture sample.`,
                gamesLine,
                sampleStatLine(matches, "bonus", "Bonus buildup", 0),
                `Position fill: ${bonusModel.missing_sample_fixtures || 0} missing fixtures × 90 minutes at ${formatNumber(bonusModel.position_prior_per_90, 3)} bonus/90.`,
                `Rate: (${formatNumber(bonusModel.early_sample_bonus_total, 3)} player bonus + ${formatNumber(bonusModel.position_prior_per_90, 3)} × ${formatNumber(bonusModel.position_fill_minutes, 0)} ÷ 90) ÷ (${formatNumber(bonusModel.early_sample_minutes_total, 0)} + ${formatNumber(bonusModel.position_fill_minutes, 0)}) × 90 = ${formatNumber(bonusModel.bonus_per_90, 3)} bonus/90.`,
                `Expected bonus: ${formatNumber(bonusModel.bonus_per_90, 3)} × ${formatNumber(bonusModel.expected_minutes)} xMins ÷ 90 = ${formatNumber(inputs.bonus_per_fixture, 3)} per fixture.`,
              ] : [
                `Source: from GW7 onward, the player's completed current-season bonus and minutes from ${label}; unfinished fixtures are excluded and double-gameweek fixtures count separately.`,
                bonusModel.season_minutes_total > 0
                  ? `Season-to-date rate: ${formatNumber(bonusModel.season_bonus_total, 3)} bonus ÷ ${formatNumber(bonusModel.season_minutes_total, 0)} minutes × 90 = ${formatNumber(bonusModel.season_bonus_per_90, 3)} bonus/90.`
                  : `Season-to-date rate: no played minutes, so ${bonusModel.fallback_source || "the available historical fallback"} supplies ${formatNumber(bonusModel.fallback_bonus_per_90, 3)} bonus/90.`,
                bonusModel.recent_six_minutes_total > 0
                  ? `Recent-six rate: ${formatNumber(bonusModel.recent_six_bonus_total, 3)} bonus ÷ ${formatNumber(bonusModel.recent_six_minutes_total, 0)} minutes × 90 = ${formatNumber(bonusModel.recent_six_bonus_per_90, 3)} bonus/90.`
                  : `Recent-six rate: no played minutes, so ${bonusModel.fallback_source || "the available historical fallback"} supplies ${formatNumber(bonusModel.fallback_bonus_per_90, 3)} bonus/90.`,
                `Player-only blend: 75% × ${formatNumber(bonusModel.season_bonus_per_90, 3)} + 25% × ${formatNumber(bonusModel.recent_six_bonus_per_90, 3)} = ${formatNumber(bonusModel.bonus_per_90, 3)} bonus/90.`,
                `Expected bonus: ${formatNumber(bonusModel.bonus_per_90, 3)} × ${formatNumber(bonusModel.expected_minutes)} xMins ÷ 90 = ${formatNumber(inputs.bonus_per_fixture, 3)} per fixture. No attacking or defensive lift is added.`,
              ],
            },
            {
              label: "Yellow cards / fixture",
              value: formatNumber(inputs.yellow_cards_per_fixture, 3),
              help: [
                `Source: yellow cards from ${sourceHistory}.`,
                gamesLine,
                sampleStatLine(matches, "yellow_cards", "Yellow-card buildup", 0),
                `Calculation: ${formatNumber(yellowModel.recent_yellow_cards_total, 0)} cards ÷ ${yellowModel.sample_size || sampleSize || 1} sampled fixtures, bounded 0–0.500 = ${formatNumber(inputs.yellow_cards_per_fixture, 3)}.`,
              ],
            },
          ])}
        </div>
      </article>
    </section>
  `;
}

function closeModal() {
  closeGlossaryTooltips();
  elements.playerModal.hidden = true;
}

function closeGlossaryTooltips(except = null) {
  elements.modalContent.querySelectorAll(".glossary-anchor.is-open").forEach((anchor) => {
    if (anchor === except) {
      return;
    }
    anchor.classList.remove("is-open");
    anchor.querySelector(".glossary-trigger")?.setAttribute("aria-expanded", "false");
  });
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

function displayedFixturePoints(fixture) {
  let total = Number(fixture.predicted_points || 0);
  if (!elements.showBonus.checked) {
    total -= Number(fixture.bonus_points || 0);
  }
  if (!elements.showYellows.checked) {
    total += Number(fixture.yellow_card_deduction || 0);
  }
  return total;
}

function getPredictorSourceData(sourceKey = state.predictor.activeSource) {
  return state.predictor.dataset?.sources?.[sourceKey] || null;
}

function predictorWindowCacheKey(sourceKey, startGameweek, endGameweek) {
  return `${sourceKey}:${startGameweek}-${endGameweek}`;
}

function getEmbeddedPredictorWindow(sourceKey, startGameweek, endGameweek) {
  const sourceData = getPredictorSourceData(sourceKey);
  const startBucket = (sourceData?.predictions || {})[String(startGameweek)] || {};
  return startBucket[String(endGameweek)] || null;
}

function getCachedPredictorWindow(sourceKey, startGameweek, endGameweek) {
  const embedded = getEmbeddedPredictorWindow(sourceKey, startGameweek, endGameweek);
  if (embedded) {
    return embedded;
  }
  return state.predictor.windowCache[predictorWindowCacheKey(sourceKey, startGameweek, endGameweek)] || null;
}

async function ensurePredictorWindowLoaded(sourceKey, startGameweek, endGameweek) {
  const cached = getCachedPredictorWindow(sourceKey, startGameweek, endGameweek);
  if (cached) {
    return cached;
  }

  const cacheKey = predictorWindowCacheKey(sourceKey, startGameweek, endGameweek);
  if (state.predictor.windowPromises[cacheKey]) {
    return state.predictor.windowPromises[cacheKey];
  }

  const sourceData = getPredictorSourceData(sourceKey);
  const relativePath = sourceData?.windows?.[String(startGameweek)]?.[String(endGameweek)];
  if (!relativePath) {
    throw new Error(`No ${sourceData?.label || sourceKey} prediction window is available for GW${startGameweek}-GW${endGameweek}.`);
  }
  const configuredBase = window.FPL_PREDICTION_WINDOWS_BASE_URL
    || state.predictor.dataset?.prediction_windows_base_url
    || "./data/prediction_windows";
  const baseUrl = configuredBase.replace(/\/+$/, "");
  const request = fetch(`${baseUrl}/${relativePath}`, { cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Prediction window request failed (${response.status})`);
      }
      const payload = relativePath.endsWith(".gz")
        ? await new Response(response.body.pipeThrough(new DecompressionStream("gzip"))).json()
        : await response.json();
      const players = Array.isArray(payload) ? payload : payload.players || [];
      state.predictor.windowCache[cacheKey] = players;
      return players;
    })
    .finally(() => {
      delete state.predictor.windowPromises[cacheKey];
    });
  state.predictor.windowPromises[cacheKey] = request;
  return request;
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
  if (state.predictor.dataset.teams?.length) {
    return [...state.predictor.dataset.teams].sort();
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
  const rows = getCachedPredictorWindow(sourceKey, selected.start, selected.end) || [];
  return rows.filter((player) => {
    const positionMatch = elements.positionFilter.value === "ALL" || player.position === elements.positionFilter.value;
    const teamMatch = state.predictor.selectedTeams.has(player.team);
    const exclusionMatch = (
      state.predictor.showExcludedPlayers
      || !state.predictor.excludedPlayerIds.has(String(player.player_id))
    );
    return positionMatch && teamMatch && exclusionMatch;
  });
}

function updateShowExcludedPlayersButton() {
  const showingExcluded = state.predictor.showExcludedPlayers;
  elements.showExcludedPlayersButton.textContent = showingExcluded
    ? "Hide excluded players"
    : "Show excluded players";
  elements.showExcludedPlayersButton.classList.toggle("is-active", showingExcluded);
  elements.showExcludedPlayersButton.setAttribute("aria-pressed", String(showingExcluded));
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

async function openPredictorPlayerModal(playerId) {
  const selected = getPredictorSelectedGameweeks();
  try {
    await Promise.all(
      Object.keys(state.predictor.dataset?.sources || {}).map((sourceKey) => (
        ensurePredictorWindowLoaded(sourceKey, selected.start, selected.end)
      ))
    );
  } catch (error) {
    elements.statusText.textContent = `Player comparison failed: ${error.message}`;
    return;
  }
  const compared = Object.entries(state.predictor.dataset?.sources || {})
    .map(([sourceKey, source]) => {
      const players = getCachedPredictorWindow(sourceKey, selected.start, selected.end) || [];
      const player = players.find((item) => String(item.player_id) === String(playerId));
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

  elements.resultsBody.innerHTML = players.map((player, index) => {
    const { givenName, surname } = splitPlayerName(player.player_name);
    const isExcluded = state.predictor.excludedPlayerIds.has(String(player.player_id));
    return `
    <tr class="${index < 5 ? "top-pick" : ""} ${isExcluded ? "is-excluded" : ""}">
      <td class="player-cell">
        <button class="player-button" type="button" data-player-id="${player.player_id}">
          <strong class="player-surname">${escapeHtml(surname)}</strong>
          ${givenName ? `<span class="player-given-name">${escapeHtml(givenName)}</span>` : ""}
        </button>
        <span class="player-meta-row">
          <span class="player-meta">${escapeHtml(player.team)} · ${escapeHtml(player.position)}</span>
          <button class="exclude-player-button" type="button" data-exclude-player-id="${player.player_id}" aria-label="${isExcluded ? "Restore" : "Exclude"} ${escapeHtml(player.player_name)}">
            ${isExcluded ? "Restore" : "Exclude"}
          </button>
        </span>
      </td>
      <td><strong>${formatNumber(displayedTotalPoints(player))}</strong></td>
      <td>${formatNumber(player.components.minutes_points)}</td>
      <td>${formatNumber(player.components.goal_points)}</td>
      <td>${formatNumber(player.components.assist_points)}</td>
      <td>${formatNumber(player.components.clean_sheet_points)}</td>
      <td>${formatNumber(player.components.defensive_contribution_points)}</td>
      <td data-cell-optional="bonus">${formatNumber(player.components.bonus_points)}</td>
      <td data-cell-optional="yellow">-${formatNumber(player.components.yellow_cards)}</td>
      <td class="fixture-list">${fixtureTilesMarkup(player.fixtures)}</td>
    </tr>
  `;
  }).join("");

  elements.playerCount.textContent = String(players.length);
  updateOptionalColumns();
  updatePredictorSortButtons();
}

function updatePredictorStatus(prefix = "Showing") {
  const dataset = state.predictor.dataset;
  const selected = getPredictorSelectedGameweeks();
  const sourceData = getPredictorSourceData();
  const sourceLabel = sourceData?.label || state.predictor.activeSource;
  const generatedAt = dataset?.generated_at ? new Date(dataset.generated_at).toLocaleString() : "unknown time";
  const sourceFetchAt = dataset?.source_last_fetch_at ? new Date(dataset.source_last_fetch_at).toLocaleString() : "unknown source fetch time";
  const cacheNote = dataset?.used_cached_data ? " Using cached source data." : "";
  const latestGameweeks = Object.values(dataset?.sources || {}).map((source) => Number(source.latest_gameweek || 0));
  const latestAcrossSources = Math.max(...latestGameweeks, 0);
  const sourceLatestGameweek = Number(sourceData?.latest_gameweek || 0);
  const sourceCoverage = sourceLatestGameweek ? ` Data through GW${sourceLatestGameweek}.` : " Data coverage unknown.";
  const lagNote = sourceLatestGameweek && latestAcrossSources > sourceLatestGameweek
    ? ` Warning: this source trails the freshest source by ${latestAcrossSources - sourceLatestGameweek} gameweek(s).`
    : "";
  elements.statusText.textContent = `${prefix} ${sourceLabel} predictions from GW${selected.start} to GW${selected.end} from ${generatedAt}.${sourceCoverage}${lagNote} Source fetch: ${sourceFetchAt}.${cacheNote}`;
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
    state.predictor.windowCache = {};
    state.predictor.windowPromises = {};
    state.predictor.refreshToken = 0;
    configurePredictorRangeControl();
    updatePredictorSourceButtons();
    renderPredictorTeamFilter();
    await refreshPredictorView("Static data updated");
  } catch (error) {
    elements.statusText.textContent = `Static data load failed: ${error.message}`;
    elements.resultsBody.innerHTML = "";
    elements.playerCount.textContent = "0";
  }
}

async function refreshPredictorView(statusPrefix = "Showing") {
  if (!state.predictor.dataset) {
    return;
  }
  const normalizedStatusPrefix = typeof statusPrefix === "string" ? statusPrefix : "Showing";
  const refreshToken = ++state.predictor.refreshToken;
  renderPredictorTeamFilter();
  renderPredictorRangeLabels();
  renderPredictorRangeFill();
  updatePredictorRangeSummary();
  const selected = getPredictorSelectedGameweeks();
  elements.statusText.textContent = `Loading ${getPredictorSourceData()?.label || state.predictor.activeSource} predictions for GW${selected.start}-GW${selected.end}...`;
  try {
    await ensurePredictorWindowLoaded(
      state.predictor.activeSource,
      selected.start,
      selected.end,
    );
    if (refreshToken !== state.predictor.refreshToken) {
      return;
    }
    renderPredictorTable();
    updatePredictorStatus(normalizedStatusPrefix);
  } catch (error) {
    if (refreshToken !== state.predictor.refreshToken) {
      return;
    }
    elements.statusText.textContent = `Prediction window load failed: ${error.message}`;
    elements.resultsBody.innerHTML = `<tr><td colspan="10">Unable to load this prediction window.</td></tr>`;
    elements.playerCount.textContent = "0";
  }
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
  const season = getActiveBacktestSeason();
  elements.backtestModeText.textContent = key && state.backtest.windowOverrides[key]
    ? "Local recompute active for this window"
    : season?.archived ? "Archived static snapshot" : "Static snapshot";
}

function getActiveBacktestSeason() {
  return state.backtest.seasons.find((season) => season.key === state.backtest.activeSeason) || null;
}

function updateBacktestRecomputeAvailability() {
  const season = getActiveBacktestSeason();
  const seasonAllowsRecompute = !season || season.recompute_available !== false;
  elements.backtestRecomputeButton.disabled = !state.backtest.localAvailable || !seasonAllowsRecompute;
  if (season && !seasonAllowsRecompute) {
    elements.backtestLocalStatus.textContent = "This archived season uses its saved model and data; local recompute is disabled to avoid mixing seasons.";
  } else if (state.backtest.localAvailable) {
    elements.backtestLocalStatus.textContent = "Local API detected. You can recompute the selected window on demand.";
  } else {
    elements.backtestLocalStatus.textContent = "Static mode only on this host. Start server.py to enable local recompute.";
  }
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
  return getWindowPayloadWithDetails(detailWindow.key, detailWindow.payload);
}

async function loadBacktestDetailWindow(key) {
  if (state.backtest.windowDetails[key]) {
    return;
  }
  state.backtest.windowDetails[key] = { loading: true };
  const summaryWindow = state.backtest.dataset?.windows?.[key];
  if (!summaryWindow) {
    return;
  }
  try {
    const season = getActiveBacktestSeason();
    const dataUrl = window.FPL_BACKTEST_WINDOWS_BASE_URL || season?.windows_base_url || "./data/backtest_windows";
    const response = await fetch(`${dataUrl}/${key}.json`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Static backtest detail request failed");
    }
    state.backtest.windowDetails[key] = {
      audit: payload.audit || {},
      sources: payload.sources || {},
    };
    if (state.activeView === "backtest") {
      refreshBacktestView();
    }
  } catch (error) {
    delete state.backtest.windowDetails[key];
    elements.backtestDetailWindowStatus.textContent = `Failed to load detail window ${key}: ${error.message}`;
  }
}

function getWindowPayloadWithDetails(key, fallbackPayload) {
  const detailOverride = state.backtest.windowDetails[key];
  if (!detailOverride || detailOverride.loading) {
    loadBacktestDetailWindow(key);
    return fallbackPayload;
  }
  return {
    ...fallbackPayload,
    audit: detailOverride.audit || fallbackPayload.audit,
    sources: {
      ...fallbackPayload.sources,
      ...detailOverride.sources,
    },
  };
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
  for (const players of Object.values(state.predictor.windowCache)) {
    const found = players.find((player) => String(player.player_id) === String(playerId));
    if (found) {
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

function getBacktestRowsForCurrentWindow() {
  const windowPayload = getActiveDetailWindowPayload();
  if (!windowPayload) {
    return [];
  }
  const rowsBySource = [];
  Object.entries(windowPayload.sources || {}).forEach(([sourceKey, sourcePayload]) => {
    rowsBySource.push(unpackBacktestRows(sourceKey, sourcePayload.rows));
  });
  const playerNameCache = new Map();
  return filterBacktestRows(rowsBySource.flat().map((row) => {
    const playerName = playerNameCache.get(row.player_id) || resolveBacktestPlayerName(row.player_id, rowsBySource);
    playerNameCache.set(row.player_id, playerName);
    return { ...row, player_name: playerName };
  }));
}

function getBacktestCombinedRowsForCurrentWindow() {
  const rows = getBacktestRowsForCurrentWindow();
  const grouped = new Map();
  rows.forEach((row) => {
    const existing = grouped.get(String(row.player_id)) || {
      player_id: row.player_id,
      player_name: row.player_name,
      team: row.team,
      position: row.position,
      actual_points: row.actual_points,
      official: null,
      elo: null,
    };
    existing.actual_points = row.actual_points;
    existing[row.source] = row;
    grouped.set(String(row.player_id), existing);
  });

  const query = state.backtest.playerQuery.trim().toLowerCase();
  return [...grouped.values()].filter((row) => {
    if (!state.backtest.selectedTeams.has(row.team)) {
      return false;
    }
    if (state.backtest.positionFilter !== "ALL" && row.position !== state.backtest.positionFilter) {
      return false;
    }
    if (!query) {
      return true;
    }
    return `${row.player_name} ${row.team} ${row.position}`.toLowerCase().includes(query);
  });
}

function ensureSelectedPlayer() {
  const combinedRows = getBacktestCombinedRowsForCurrentWindow();
  if (combinedRows.length === 0) {
    state.backtest.selectedPlayerId = null;
    return null;
  }
  const existing = combinedRows.find((row) => String(row.player_id) === String(state.backtest.selectedPlayerId));
  if (existing) {
    return existing;
  }
  const sorted = [...combinedRows].sort((left, right) => left.player_name.localeCompare(right.player_name));
  state.backtest.selectedPlayerId = sorted[0].player_id;
  return sorted[0];
}

function renderBacktestPlayerSelect() {
  const combinedRows = [...getBacktestCombinedRowsForCurrentWindow()].sort((left, right) => left.player_name.localeCompare(right.player_name));
  if (combinedRows.length === 0) {
    elements.backtestPlayerSelect.innerHTML = `<option value="">No players available</option>`;
    state.backtest.selectedPlayerId = null;
    return;
  }
  ensureSelectedPlayer();
  elements.backtestPlayerSelect.innerHTML = combinedRows.map((row) => `
    <option value="${row.player_id}" ${String(row.player_id) === String(state.backtest.selectedPlayerId) ? "selected" : ""}>
      ${escapeHtml(`${row.player_name} · ${row.team} · ${row.position}`)}
    </option>
  `).join("");
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
  const selectedPlayer = ensureSelectedPlayer();
  if (!selectedPlayer) {
    elements.backtestSummaryCards.innerHTML = `
      <article class="metric-card">
        <h3>No player selected</h3>
        <p class="control-note">Adjust the filters or select a player to inspect a single rolling window.</p>
      </article>
    `;
    return;
  }

  const sourceOrder = ["official", "elo"];
  elements.backtestSummaryCards.innerHTML = sourceOrder.map((sourceKey) => {
    const row = selectedPlayer[sourceKey];
    if (!row) {
      return "";
    }
    const label = state.backtest.dataset.sources[sourceKey];
    const errorClass = row.error <= 0 ? "metric-good" : "metric-bad";
    return `
      <article class="metric-card">
        <div class="source-kicker">${escapeHtml(label)}</div>
        <div class="metric-main">
          <div>
            <div class="muted">${escapeHtml(selectedPlayer.player_name)}</div>
            <strong>${formatNumber(row.absolute_error, 3)}</strong>
          </div>
          <div class="${errorClass}">
            ${formatSigned(row.error)}
          </div>
        </div>
        <div class="metric-list">
          ${detailRows([
            ["Predicted points", formatNumber(row.predicted_points)],
            ["Actual points", formatNumber(row.actual_points)],
            ["Absolute error", formatNumber(row.absolute_error)],
            ["Predicted rank", row.predicted_rank],
            ["Actual rank", row.actual_rank],
            ["Rank error", formatSigned(row.rank_error, 0)],
            ["Window", `GW${ensureActiveDetailWindow()?.start_gw} to GW${ensureActiveDetailWindow()?.end_gw}`],
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
  const selectedPlayer = ensureSelectedPlayer();
  const series = {
    official: [],
    elo: [],
    actual: [],
  };

  if (!selectedPlayer) {
    elements.backtestTrendChart.innerHTML = `<div class="empty-state">Select a player to plot rolling ${horizon}-GW projections.</div>`;
    elements.backtestSpanChart.innerHTML = `<div class="empty-state">Select a player to compare rolling-window MAE by span.</div>`;
    return;
  }

  windows.forEach((windowEntry) => {
    const payloadWithDetails = getWindowPayloadWithDetails(windowEntry.key, windowEntry.payload);
    Object.entries(payloadWithDetails.sources || {}).forEach(([sourceKey]) => {
      const row = sourceRowsForWindow(payloadWithDetails, sourceKey).find((item) => String(item.player_id) === String(selectedPlayer.player_id));
      if (!row) {
        return;
      }
      series[sourceKey].push({
        start_gw: windowEntry.start_gw,
        end_gw: windowEntry.end_gw,
        span: horizon,
        key: windowEntry.key,
        mae: Number(row.absolute_error || 0),
        total_points: Number(row.predicted_points || 0),
      });
    });
    const officialRow = sourceRowsForWindow(payloadWithDetails, "official").find((item) => String(item.player_id) === String(selectedPlayer.player_id));
    const fallbackRow = officialRow || sourceRowsForWindow(payloadWithDetails, "elo").find((item) => String(item.player_id) === String(selectedPlayer.player_id));
    if (fallbackRow) {
      series.actual.push({
        start_gw: windowEntry.start_gw,
        end_gw: windowEntry.end_gw,
        span: horizon,
        key: windowEntry.key,
        mae: 0,
        total_points: Number(fallbackRow.actual_points || 0),
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

  const spanSeries = { official: [], elo: [] };
  const rangeWindows = getBacktestRangeWindows();
  ["official", "elo"].forEach((sourceKey) => {
    const grouped = new Map();
    rangeWindows.forEach((windowEntry) => {
      const payloadWithDetails = getWindowPayloadWithDetails(windowEntry.key, windowEntry);
      const row = sourceRowsForWindow(payloadWithDetails, sourceKey).find((item) => String(item.player_id) === String(selectedPlayer.player_id));
      if (!row) {
        return;
      }
      if (!grouped.has(windowEntry.span)) {
        grouped.set(windowEntry.span, []);
      }
      grouped.get(windowEntry.span).push(Number(row.absolute_error || 0));
    });
    spanSeries[sourceKey] = [...grouped.entries()]
      .map(([spanValue, values]) => ({ span: spanValue, value: mean(values), windows: values.length }))
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
  elements.backtestTrendNote.textContent += ` Selected player: ${selectedPlayer.player_name}.`;
  elements.backtestSpanNote.textContent = `This chart averages ${selectedPlayer.player_name}'s absolute error across all valid windows inside the selected range by span length.`;
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

function renderBacktestExplorerTable() {
  const rows = [...getBacktestCombinedRowsForCurrentWindow()];
  rows.sort((left, right) => {
    const leftValue = Math.min(Math.abs(left.official?.error ?? Infinity), Math.abs(left.elo?.error ?? Infinity));
    const rightValue = Math.min(Math.abs(right.official?.error ?? Infinity), Math.abs(right.elo?.error ?? Infinity));
    return rightValue - leftValue;
  });

  if (rows.length === 0) {
    elements.backtestExplorerBody.innerHTML = `<tr><td colspan="8">No player rows are available for this filter combination.</td></tr>`;
    return;
  }

  ensureSelectedPlayer();
  elements.backtestExplorerBody.innerHTML = rows.map((row) => `
    <tr class="${String(row.player_id) === String(state.backtest.selectedPlayerId) ? "top-pick" : ""}">
      <td>
        <button class="player-button" type="button" data-backtest-player-id="${row.player_id}">
          <strong>${escapeHtml(row.player_name)}</strong>
        </button>
      </td>
      <td>${escapeHtml(row.team)}</td>
      <td>${escapeHtml(row.position)}</td>
      <td>${formatNumber(row.official?.predicted_points ?? 0)}</td>
      <td>${formatSigned(row.official?.error ?? 0)}</td>
      <td>${formatNumber(row.elo?.predicted_points ?? 0)}</td>
      <td>${formatSigned(row.elo?.error ?? 0)}</td>
      <td>${formatNumber(row.actual_points)}</td>
    </tr>
  `).join("");
}

function sourceRowsForWindow(windowPayload, sourceKey) {
  const sourcePayload = windowPayload?.sources?.[sourceKey];
  return sourcePayload?.rows ? unpackBacktestRows(sourceKey, sourcePayload.rows) : [];
}

function filterBacktestRows(rows, { applyQuery = true } = {}) {
  const query = state.backtest.playerQuery.trim().toLowerCase();
  return rows.filter((row) => {
    if (state.backtest.positionFilter !== "ALL" && row.position !== state.backtest.positionFilter) {
      return false;
    }
    if (!state.backtest.selectedTeams.has(row.team)) {
      return false;
    }
    if (!applyQuery || !query) {
      return true;
    }
    return `${row.player_name} ${row.team} ${row.position} ${row.source_label}`.toLowerCase().includes(query);
  });
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

function mergeAttributionTotals(target, source) {
  target.predicted_points += source.predicted_points;
  target.actual_points += source.actual_points;
  Object.keys(target.predicted_components).forEach((key) => {
    target.predicted_components[key] += source.predicted_components[key] || 0;
    target.actual_components[key] += source.actual_components[key] || 0;
  });
  Object.keys(target.predicted_stats).forEach((key) => {
    target.predicted_stats[key] += source.predicted_stats[key] || 0;
    target.actual_stats[key] += source.actual_stats[key] || 0;
  });
}

function attributionRows(official, elo, actual) {
  const metrics = [
    ["Total points", official.predicted_points, elo.predicted_points, actual.actual_points],
    ["Minutes points", official.predicted_components.minutes_points, elo.predicted_components.minutes_points, actual.actual_components.minutes_points],
    ["Goal points", official.predicted_components.goal_points, elo.predicted_components.goal_points, actual.actual_components.goal_points],
    ["Assist points", official.predicted_components.assist_points, elo.predicted_components.assist_points, actual.actual_components.assist_points],
    ["Clean-sheet points", official.predicted_components.clean_sheet_points, elo.predicted_components.clean_sheet_points, actual.actual_components.clean_sheet_points],
    ["Defcon points", official.predicted_components.defensive_contribution_points, elo.predicted_components.defensive_contribution_points, actual.actual_components.defensive_contribution_points],
    ["Bonus points", official.predicted_components.bonus_points, elo.predicted_components.bonus_points, actual.actual_components.bonus_points],
    ["Yellow deduction", official.predicted_components.yellow_deduction, elo.predicted_components.yellow_deduction, actual.actual_components.yellow_deduction],
    ["Other points", official.predicted_components.other_points, elo.predicted_components.other_points, actual.actual_components.other_points],
    ["xG", official.predicted_stats.expected_goals, elo.predicted_stats.expected_goals, actual.actual_stats.expected_goals],
    ["xA", official.predicted_stats.expected_assists, elo.predicted_stats.expected_assists, actual.actual_stats.expected_assists],
  ];

  return metrics.map(([label, op, ep, av]) => `
    <tr>
      <td>${escapeHtml(label)}</td>
      <td>${formatNumber(op, 2)}</td>
      <td>${formatNumber(ep, 2)}</td>
      <td>${formatNumber(av, 2)}</td>
    </tr>
  `).join("");
}

function computeComponentVariance(rows) {
  const totals = aggregateComponentRows(rows);
  const categories = [
    ["minutes_points", "Minutes"],
    ["goal_points", "Goals"],
    ["assist_points", "Assists"],
    ["clean_sheet_points", "Clean sheets"],
    ["defensive_contribution_points", "Defcon"],
    ["bonus_points", "Bonus"],
    ["yellow_deduction", "Yellows"],
    ["other_points", "Other"],
  ];
  return categories.map(([key, label]) => ({
    key,
    label,
    value: Math.abs((totals.predicted_components[key] || 0) - (totals.actual_components[key] || 0)),
  })).filter((item) => item.value > 0.001);
}

function donutChartMarkup(slices, total, label) {
  if (!slices.length || total <= 0) {
    return `<div class="empty-state">No variance for ${escapeHtml(label)}.</div>`;
  }
  const colors = ["#14213d", "#ff7a00", "#1b7f5b", "#c46b00", "#5b6cfa", "#b85c38", "#7a8b9c", "#c6b59c"];
  const radius = 42;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const arcs = slices.map((slice, index) => {
    const dash = (slice.value / total) * circumference;
    const segment = `
      <circle
        cx="56"
        cy="56"
        r="${radius}"
        fill="none"
        stroke="${colors[index % colors.length]}"
        stroke-width="16"
        stroke-dasharray="${dash} ${circumference - dash}"
        stroke-dashoffset="${-offset}"
        transform="rotate(-90 56 56)"
      >
        <title>${escapeHtml(slice.label)}: ${formatNumber(slice.value, 2)}</title>
      </circle>
    `;
    offset += dash;
    return segment;
  }).join("");

  const legend = slices.map((slice, index) => `
    <div class="variance-item">
      <span class="variance-label">
        <span class="legend-dot" style="background:${colors[index % colors.length]}"></span>
        ${escapeHtml(slice.label)}
      </span>
      <strong>${formatNumber(slice.value, 2)} · ${formatNumber((slice.value / total) * 100, 1)}%</strong>
    </div>
  `).join("");

  return `
    <svg class="donut-svg" viewBox="0 0 112 112" role="img" aria-label="${escapeHtml(label)} variance by component">
      <circle cx="56" cy="56" r="${radius}" fill="none" stroke="rgba(217, 205, 189, 0.5)" stroke-width="16"></circle>
      ${arcs}
      <text x="56" y="52" text-anchor="middle" class="donut-center">${escapeHtml(label)}</text>
      <text x="56" y="68" text-anchor="middle" class="donut-center">${formatNumber(total, 1)}</text>
    </svg>
    <div class="variance-list">${legend}</div>
  `;
}

function renderBacktestVarianceChart() {
  if (!state.backtest.dataset) {
    elements.backtestVarianceGrid.innerHTML = "";
    return;
  }
  const selected = getBacktestSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    elements.backtestVarianceGrid.innerHTML = `<div class="empty-state">No historical range selected.</div>`;
    return;
  }

  const selectedPlayer = ensureSelectedPlayer();
  const officialRows = [];
  const eloRows = [];
  for (let gw = selected.start; gw <= selected.end; gw += 1) {
    const key = backtestWindowKey(gw, gw);
    const payload = getBacktestWindowPayload(key);
    if (!payload) {
      continue;
    }
    const payloadWithDetails = getWindowPayloadWithDetails(key, payload);
    let windowOfficialRows = filterBacktestRows(sourceRowsForWindow(payloadWithDetails, "official"), { applyQuery: false });
    let windowEloRows = filterBacktestRows(sourceRowsForWindow(payloadWithDetails, "elo"), { applyQuery: false });
    if (selectedPlayer) {
      windowOfficialRows = windowOfficialRows.filter((row) => String(row.player_id) === String(selectedPlayer.player_id));
      windowEloRows = windowEloRows.filter((row) => String(row.player_id) === String(selectedPlayer.player_id));
    }
    officialRows.push(...windowOfficialRows);
    eloRows.push(...windowEloRows);
  }

  const officialVariance = computeComponentVariance(officialRows);
  const eloVariance = computeComponentVariance(eloRows);
  const officialTotal = officialVariance.reduce((sum, item) => sum + item.value, 0);
  const eloTotal = eloVariance.reduce((sum, item) => sum + item.value, 0);
  const sampleCount = Math.max(officialRows.length, eloRows.length);
  const scopeLabel = selectedPlayer
    ? `${selectedPlayer.player_name} from GW${selected.start} to GW${selected.end}`
    : `${sampleCount} filtered player-window rows from GW${selected.start} to GW${selected.end}`;

  elements.backtestVarianceNote.textContent = `This shows total absolute variance by FPL scoring component for ${scopeLabel}. Percentages are each component's share of total variance.`;
  elements.backtestVarianceGrid.innerHTML = (officialRows.length || eloRows.length)
    ? `
      <div class="variance-grid">
        <article class="variance-card">
          <div class="variance-head">
            <strong>Official FPL</strong>
            <span class="muted">${sampleCount} GW samples</span>
          </div>
          ${donutChartMarkup(officialVariance, officialTotal, "Official")}
        </article>
        <article class="variance-card">
          <div class="variance-head">
            <strong>Elo Insights</strong>
            <span class="muted">${sampleCount} GW samples</span>
          </div>
          ${donutChartMarkup(eloVariance, eloTotal, "Elo")}
        </article>
      </div>
    `
    : `<div class="empty-state">No component variance is available for the current range and filters.</div>`;
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

  const selectedPlayer = ensureSelectedPlayer();
  if (!selectedPlayer) {
    elements.backtestDetailWindowStatus.textContent = "Select a player to inspect a detailed attribution breakdown.";
    elements.backtestDetailComponentsBody.innerHTML = "";
    elements.backtestAggregateStatus.textContent = "Select a player to inspect aggregate attribution across the selected range.";
    elements.backtestAggregateComponentsBody.innerHTML = "";
    return;
  }

  const detailPayload = getActiveDetailWindowPayload();
  const detailOfficialRow = sourceRowsForWindow(detailPayload, "official").find((row) => String(row.player_id) === String(selectedPlayer.player_id));
  const detailEloRow = sourceRowsForWindow(detailPayload, "elo").find((row) => String(row.player_id) === String(selectedPlayer.player_id));
  const detailOfficial = aggregateComponentRows(detailOfficialRow ? [detailOfficialRow] : []);
  const detailElo = aggregateComponentRows(detailEloRow ? [detailEloRow] : []);
  const detailActual = aggregateComponentRows(detailOfficialRow ? [detailOfficialRow] : detailEloRow ? [detailEloRow] : []);
  elements.backtestDetailWindowStatus.textContent = `Detail window: GW${detailWindow.start_gw} to GW${detailWindow.end_gw} for ${selectedPlayer.player_name}. Click a point on the trend chart to inspect a different start gameweek.`;
  elements.backtestDetailComponentsBody.innerHTML = attributionRows(detailOfficial, detailElo, detailActual);

  const horizonWindows = getBacktestHorizonWindows();
  const aggregateOfficial = emptyAttribution();
  const aggregateElo = emptyAttribution();
  const aggregateActual = emptyAttribution();
  horizonWindows.forEach((windowEntry) => {
    const payloadWithDetails = getWindowPayloadWithDetails(windowEntry.key, windowEntry.payload);
    const officialRow = sourceRowsForWindow(payloadWithDetails, "official").find((row) => String(row.player_id) === String(selectedPlayer.player_id));
    const eloRow = sourceRowsForWindow(payloadWithDetails, "elo").find((row) => String(row.player_id) === String(selectedPlayer.player_id));
    if (officialRow) {
      mergeAttributionTotals(aggregateOfficial, aggregateComponentRows([officialRow]));
      mergeAttributionTotals(aggregateActual, aggregateComponentRows([officialRow]));
    }
    if (eloRow) {
      mergeAttributionTotals(aggregateElo, aggregateComponentRows([eloRow]));
      if (!officialRow) {
        mergeAttributionTotals(aggregateActual, aggregateComponentRows([eloRow]));
      }
    }
  });
  elements.backtestAggregateStatus.textContent = `Aggregate across ${horizonWindows.length} rolling ${getValidBacktestHorizon()}-GW window${horizonWindows.length === 1 ? "" : "s"} for ${selectedPlayer.player_name}.`;
  elements.backtestAggregateComponentsBody.innerHTML = attributionRows(aggregateOfficial, aggregateElo, aggregateActual);
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
  renderBacktestPlayerSelect();
  renderBacktestRangeLabels();
  renderBacktestRangeFill();
  updateBacktestRangeSummary();
  renderBacktestSummaryCards();
  renderBacktestTrendChart();
  renderBacktestVarianceChart();
  renderBacktestAttributionTables();
  renderBacktestExplorerTable();

  const generatedAt = state.backtest.dataset.generated_at ? new Date(state.backtest.dataset.generated_at).toLocaleString() : "unknown time";
  const selected = getBacktestSelectedGameweeks();
  if (selected.start === null || selected.end === null) {
    elements.backtestStatusText.textContent = `No finished gameweeks are available in the ${generatedAt} backtest snapshot.`;
    return;
  }
  const audit = detailWindow?.payload?.audit || {};
  const horizon = getValidBacktestHorizon();
  const auditText = audit.common_players
    ? ` Official vs Elo: ${audit.different_prediction_matches} of ${audit.common_players} player predictions differ in this window (max delta ${formatNumber(audit.max_prediction_delta)}).`
    : "";
  const detailText = detailWindow ? ` Active detail window: GW${detailWindow.start_gw} to GW${detailWindow.end_gw}.` : "";
  elements.backtestStatusText.textContent = `Showing rolling ${horizon}-GW projections from GW${selected.start} to GW${selected.end} from the ${generatedAt} backtest snapshot.${detailText}${auditText}`;
}

async function loadBacktestSeason(seasonKey) {
  if (state.backtest.isLoading) {
    return;
  }
  state.backtest.isLoading = true;
  const season = state.backtest.seasons.find((item) => item.key === seasonKey);
  const dataUrl = window.FPL_BACKTEST_DATA_URL || season?.data_url || "./data/static_backtest.json";
  elements.backtestStatusText.textContent = "Loading static backtest data...";
  try {
    const response = await fetch(dataUrl, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "Static backtest request failed");
    }
    state.backtest.dataset = payload;
    state.backtest.activeSeason = season?.key || seasonKey;
    state.backtest.selectedTeams = new Set();
    state.backtest.teamsInitialized = false;
    state.backtest.windowDetails = {};
    state.backtest.windowOverrides = {};
    state.backtest.activeDetailStartGw = null;
    buildBacktestAllTeams();
    configureBacktestRangeControl();
    renderBacktestTeamFilter();
    refreshBacktestView();
    state.backtest.hasLoaded = true;
    elements.backtestSeasonSelect.value = state.backtest.activeSeason;
    updateBacktestRecomputeAvailability();
  } catch (error) {
    elements.backtestStatusText.textContent = `Static backtest load failed: ${error.message}`;
    elements.backtestSummaryCards.innerHTML = "";
    elements.backtestTrendChart.innerHTML = "";
    elements.backtestSpanChart.innerHTML = "";
    elements.backtestVarianceGrid.innerHTML = "";
    elements.backtestExplorerBody.innerHTML = "";
  } finally {
    state.backtest.isLoading = false;
  }
}

async function loadBacktestData() {
  if (state.backtest.isLoading || state.backtest.hasLoaded) {
    return;
  }
  if (window.FPL_BACKTEST_DATA_URL) {
    state.backtest.seasons = [{ key: "custom", label: "Custom dataset", data_url: window.FPL_BACKTEST_DATA_URL }];
    state.backtest.activeSeason = "custom";
  } else {
    try {
      const response = await fetch("./data/backtest_seasons.json", { cache: "no-store" });
      const manifest = await response.json();
      if (!response.ok || !manifest.seasons?.length) {
        throw new Error("No backtest seasons are listed");
      }
      state.backtest.seasons = manifest.seasons;
      state.backtest.activeSeason = manifest.default_season || manifest.seasons[0].key;
    } catch (error) {
      state.backtest.seasons = [{ key: "default", label: "Published snapshot", data_url: "./data/static_backtest.json", windows_base_url: "./data/backtest_windows" }];
      state.backtest.activeSeason = "default";
    }
  }
  elements.backtestSeasonSelect.innerHTML = state.backtest.seasons
    .map((season) => `<option value="${escapeHtml(season.key)}">${escapeHtml(season.label || season.key)}</option>`)
    .join("");
  elements.backtestSeasonSelect.value = state.backtest.activeSeason;
  await loadBacktestSeason(state.backtest.activeSeason);
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
    updateBacktestRecomputeAvailability();
  } catch (error) {
    state.backtest.localAvailable = false;
    updateBacktestRecomputeAvailability();
  }
}

async function recomputeBacktestWindow() {
  const season = getActiveBacktestSeason();
  if (!state.backtest.localAvailable || season?.recompute_available === false) {
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

elements.showExcludedPlayersButton.addEventListener("click", () => {
  state.predictor.showExcludedPlayers = !state.predictor.showExcludedPlayers;
  updateShowExcludedPlayersButton();
  renderPredictorTable();
});

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
  const exclusionButton = event.target.closest("[data-exclude-player-id]");
  if (exclusionButton) {
    const playerId = String(exclusionButton.dataset.excludePlayerId);
    if (state.predictor.excludedPlayerIds.has(playerId)) {
      state.predictor.excludedPlayerIds.delete(playerId);
    } else {
      state.predictor.excludedPlayerIds.add(playerId);
    }
    const exclusionSaved = saveExcludedPlayerIds();
    renderPredictorTable();
    if (!exclusionSaved) {
      elements.statusText.textContent = "This browser could not save the exclusion preference.";
    }
    return;
  }
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
  state.backtest.selectedPlayerId = null;
  refreshBacktestView();
});

elements.backtestPlayerSearch.addEventListener("input", () => {
  state.backtest.playerQuery = elements.backtestPlayerSearch.value;
  state.backtest.selectedPlayerId = null;
  refreshBacktestView();
});

elements.backtestPlayerSelect.addEventListener("change", () => {
  state.backtest.selectedPlayerId = elements.backtestPlayerSelect.value || null;
  refreshBacktestView();
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
  state.backtest.selectedPlayerId = null;
  refreshBacktestView();
});

elements.backtestSelectAllTeamsButton.addEventListener("click", () => {
  state.backtest.selectedTeams = new Set(state.backtest.allTeams);
  state.backtest.selectedPlayerId = null;
  refreshBacktestView();
});

elements.backtestClearAllTeamsButton.addEventListener("click", () => {
  state.backtest.selectedTeams = new Set();
  state.backtest.selectedPlayerId = null;
  refreshBacktestView();
});

elements.backtestExplorerBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-backtest-player-id]");
  if (button) {
    state.backtest.selectedPlayerId = button.dataset.backtestPlayerId;
    refreshBacktestView();
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
elements.backtestSeasonSelect.addEventListener("change", async () => {
  state.backtest.hasLoaded = false;
  await loadBacktestSeason(elements.backtestSeasonSelect.value);
});
elements.closeModalButton.addEventListener("click", closeModal);
elements.modalContent.addEventListener("click", (event) => {
  const trigger = event.target.closest(".glossary-trigger");
  if (!trigger) {
    closeGlossaryTooltips();
    return;
  }
  const anchor = trigger.closest(".glossary-anchor");
  const willOpen = !anchor.classList.contains("is-open");
  closeGlossaryTooltips(anchor);
  anchor.classList.toggle("is-open", willOpen);
  trigger.setAttribute("aria-expanded", String(willOpen));
});
elements.playerModal.addEventListener("click", (event) => {
  if (event.target === elements.playerModal) {
    closeModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.playerModal.hidden) {
    const openGlossary = elements.modalContent.querySelector(".glossary-anchor.is-open");
    if (openGlossary) {
      closeGlossaryTooltips();
      return;
    }
    closeModal();
  }
});

updateOptionalColumns();
switchView(new URLSearchParams(window.location.search).get("view") === "backtest" ? "backtest" : "predictor");
loadPredictions();
updateShowExcludedPlayersButton();
