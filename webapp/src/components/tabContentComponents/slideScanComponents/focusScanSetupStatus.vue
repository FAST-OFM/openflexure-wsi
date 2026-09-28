<template>
  <section v-if="mode === 'live'" class="focus-runtime uk-margin-small-top">
    <h4 class="uk-margin-small-bottom">Current scan focus</h4>
    <p v-if="!focus" class="uk-text-muted uk-margin-remove">Focus status is not available yet.</p>
    <template v-else>
      <p class="uk-margin-remove-bottom">
        <span class="uk-label" :class="runtimeLabelClass">{{ runtimeLabel }}</span>
        <span class="uk-text-meta uk-margin-small-left">{{ focus.reason }}</span>
      </p>
      <dl class="focus-summary-list uk-margin-small-top">
        <div>
          <dt>Actual path</dt>
          <dd>{{ formatPath(focus.path) }}</dd>
        </div>
        <div>
          <dt>Stage</dt>
          <dd>{{ formatValue(focus.stage) }}</dd>
        </div>
        <div v-if="focus.predicted_z_um !== null">
          <dt>Predicted Z</dt>
          <dd>{{ focus.predicted_z_um }} µm</dd>
        </div>
        <div v-if="focus.measured_z_um !== null">
          <dt>Measured Z</dt>
          <dd>{{ focus.measured_z_um }} µm</dd>
        </div>
        <div v-if="focus.final_z_um !== null">
          <dt>Final Z</dt>
          <dd>{{ focus.final_z_um }} µm</dd>
        </div>
        <div v-if="focus.white_focus_z_um !== null">
          <dt>WHITE focus Z</dt>
          <dd>{{ focus.white_focus_z_um }} µm</dd>
        </div>
        <div>
          <dt>WHITE searches this field</dt>
          <dd>{{ focus.white_search_count }} / 1 fixed</dd>
        </div>
      </dl>
      <div v-if="focus.counters" class="focus-counters uk-text-small">
        <span>Fields {{ focus.counters.fields_planned }}</span>
        <span>No tissue {{ focus.counters.fields_no_tissue }}</span>
        <span>Focused {{ focus.counters.fields_focused }}</span>
        <span>Captured {{ focus.counters.fields_captured }}</span>
        <span>Failed {{ focus.counters.fields_failed }}</span>
        <span>Cancelled {{ focus.counters.fields_cancelled }}</span>
        <span>Unknown {{ focus.counters.fields_unknown }}</span>
        <span>WHITE completed {{ focus.counters.white_searches_completed }}</span>
      </div>
      <p v-if="focus.scan_reason" class="uk-text-meta uk-margin-small-top uk-margin-remove-bottom">
        Scan: {{ focus.scan_reason }}
      </p>
    </template>
  </section>

  <ul v-else-if="workflowName" class="uk-margin-small-top" uk-accordion="multiple: true">
    <li>
      <a class="uk-accordion-title" href="#"> Advanced focus map </a>
      <div class="uk-accordion-content">
        <compact-help
          label="Next scan only"
          text="Apply writes and reads back one complete setup for the next scan. It never changes a running scan."
        />

        <p v-if="loading" class="uk-text-muted">Loading saved next-scan setup…</p>
        <p v-else-if="!draft && applyMessage" class="uk-text-danger">{{ applyMessage }}</p>
        <template v-else-if="draft">
          <p class="uk-margin-small">
            <span class="uk-label" :class="draft.enabled ? 'uk-label-warning' : ''">
              {{ draft.enabled ? "Enabled for next scan" : "Off for next scan" }}
            </span>
            <span v-if="dirty" class="uk-text-warning uk-margin-small-left">Unsaved draft</span>
            <span v-else class="uk-text-meta uk-margin-small-left">Saved readback</span>
          </p>

          <div v-if="capability && capability.reasons.length" class="uk-alert-warning" uk-alert>
            <p class="uk-margin-remove-bottom">Cannot enable with the current selection/binding:</p>
            <ul class="uk-margin-small-top">
              <li v-for="reason in capability.reasons" :key="reason">{{ reason }}</li>
            </ul>
          </div>
          <div
            v-if="capability && capability.saved_binding_current === false"
            class="uk-alert-danger"
            uk-alert
          >
            Saved setup is stale: {{ capability.saved_binding_reason }}
            <span v-if="capability.saved_binding_mismatched_fields.length">
              ({{ capability.saved_binding_mismatched_fields.join(", ") }})
            </span>
          </div>

          <div class="focus-form-grid">
            <label class="focus-checkbox">
              <input
                v-model="draft.enabled"
                class="uk-checkbox"
                type="checkbox"
                @change="enabledChanged"
              />
              Use focus map + joint XY/Z preload
            </label>
            <label>
              Missing prediction
              <select
                v-model="draft.unpredictedFocusMode"
                class="uk-select uk-form-small"
                :disabled="!draft.enabled"
              >
                <option value="selected_method">Use selected R/G method</option>
                <option value="white_then_led">One WHITE search, then R/G</option>
              </select>
            </label>
            <label>
              No tissue
              <select v-model="draft.noTissueMode" class="uk-select uk-form-small">
                <option value="keep_z">Keep Z and capture WHITE</option>
                <option value="skip_tile">Keep Z and skip tile</option>
                <option value="pause">Pause scan</option>
              </select>
            </label>
          </div>

          <compact-help
            class="uk-margin-small-top"
            label="How it works"
            text="Off keeps one-pair R/G focus. On seeds a surface map from accepted fields, moves XY and Z toward the predicted preload point together, then finishes with the short one-direction Z approach."
          />

          <dl class="focus-readonly uk-margin-small-top">
            <div>
              <dt>Selected autofocus</dt>
              <dd>{{ capability?.autofocus_method || "Unavailable" }}</dd>
            </div>
            <div>
              <dt>Selected strategy</dt>
              <dd>{{ capability?.focus_strategy || "Unavailable" }}</dd>
            </div>
            <div>
              <dt>Focus failure policy</dt>
              <dd>Pause (fixed)</dd>
            </div>
            <div>
              <dt>WHITE searches per field</dt>
              <dd>1 (fixed invariant)</dd>
            </div>
          </dl>

          <details class="focus-details uk-margin-small-top">
            <summary>Map support and prediction gates</summary>
            <div class="focus-form-grid uk-margin-small-top">
              <label class="focus-checkbox">
                <input v-model="draft.allowExtrapolation" class="uk-checkbox" type="checkbox" />
                Allow bounded extrapolation
              </label>
              <label v-for="field in mapFields" :key="field.key">
                {{ field.label }}<span v-if="field.unit"> ({{ field.unit }})</span>
                <input
                  v-model="draft[field.key]"
                  class="uk-input uk-form-small"
                  type="number"
                  :step="field.step"
                  inputmode="decimal"
                />
              </label>
            </div>
          </details>

          <details class="focus-details uk-margin-small-top">
            <summary>Scan-local envelope and budgets</summary>
            <div class="focus-form-grid uk-margin-small-top">
              <label v-for="field in budgetFields" :key="field.key">
                {{ field.label }}<span v-if="field.unit"> ({{ field.unit }})</span>
                <input
                  v-model="draft[field.key]"
                  class="uk-input uk-form-small"
                  type="number"
                  :step="field.step"
                  inputmode="decimal"
                />
              </label>
            </div>
            <p class="uk-text-meta uk-margin-small-top">
              These bounds limit this scan contract; they do not change physical stage settings.
            </p>
          </details>

          <details class="focus-details uk-margin-small-top">
            <summary>WHITE → R/G transfer</summary>
            <p class="uk-text-meta uk-margin-small-top">
              Usable only when prediction is enabled and “one WHITE search, then R/G” is selected. A
              failed R/G attempt never starts WHITE.
            </p>
            <div class="focus-form-grid">
              <label v-for="field in whiteFields" :key="field.key">
                {{ field.label }}<span v-if="field.unit"> ({{ field.unit }})</span>
                <input
                  v-model="draft[field.key]"
                  class="uk-input uk-form-small"
                  type="number"
                  :step="field.step"
                  inputmode="decimal"
                  :disabled="!whiteControlsEnabled"
                />
              </label>
            </div>
          </details>

          <details v-if="bindingRows.length" class="focus-details uk-margin-small-top">
            <summary>Current physical binding (read-only)</summary>
            <dl class="focus-readonly uk-margin-small-top">
              <div v-for="row in bindingRows" :key="row.label">
                <dt>{{ row.label }}</dt>
                <dd>{{ row.value }}</dd>
              </div>
            </dl>
          </details>

          <div class="uk-button-group uk-margin-top">
            <button
              class="uk-button uk-button-primary uk-button-small"
              type="button"
              :disabled="applying || (draft.enabled && !capability?.available)"
              @click="applyDraft"
            >
              Apply and read back
            </button>
            <button
              class="uk-button uk-button-default uk-button-small"
              type="button"
              :disabled="applying || !savedSettings?.run?.surface?.enabled"
              @click="disableNextScan"
            >
              Disable next scan
            </button>
            <button
              class="uk-button uk-button-default uk-button-small"
              type="button"
              :disabled="applying"
              @click="resetNextScan"
            >
              Reset next setup
            </button>
            <button
              class="uk-button uk-button-text uk-button-small"
              type="button"
              :disabled="applying"
              @click="loadSetup"
            >
              Refresh
            </button>
          </div>
          <p
            v-if="applyMessage"
            class="uk-text-small uk-margin-small-top"
            :class="applyState === 'failed' ? 'uk-text-danger' : 'uk-text-success'"
          >
            {{ applyMessage }}
          </p>
        </template>
      </div>
    </li>
  </ul>
</template>

<script>
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
const mapFields = [
  { key: "minimumPlanePoints", label: "Minimum plane points", step: 1, integer: true },
  { key: "maximumNeighbors", label: "Maximum neighbours", step: 1, integer: true },
  {
    key: "maximumCandidateObservations",
    label: "Maximum candidate observations",
    step: 1,
    integer: true,
  },
  { key: "neighborRadiusUm", label: "Neighbour radius", unit: "µm", step: "any" },
  { key: "maximumObservationAgeS", label: "Maximum observation age", unit: "s", step: "any" },
  { key: "maximumExtrapolationUm", label: "Maximum extrapolation", unit: "µm", step: "any" },
  { key: "maximumPredictionDeltaUm", label: "Maximum prediction delta", unit: "µm", step: "any" },
  { key: "measurementOffsetUm", label: "R/G measurement offset", unit: "µm", step: "any" },
  { key: "maximumFitResidualUm", label: "Maximum fit residual", unit: "µm", step: "any" },
  {
    key: "minimumFitInlierFraction",
    label: "Minimum fit inlier fraction",
    unit: "0–1",
    step: "any",
  },
  { key: "maximumFitConditionNumber", label: "Maximum fit condition number", step: "any" },
  {
    key: "maximumPlaneSlopeUmPerUm",
    label: "Maximum plane slope",
    unit: "µm/µm",
    step: "any",
  },
  {
    key: "predictionErrorLowUm",
    label: "Expected prediction error lower",
    unit: "µm",
    step: "any",
  },
  {
    key: "predictionErrorHighUm",
    label: "Expected prediction error upper",
    unit: "µm",
    step: "any",
  },
];

const budgetFields = [
  { key: "envelopeXLowUm", label: "Envelope X lower", unit: "µm", step: "any" },
  { key: "envelopeXHighUm", label: "Envelope X upper", unit: "µm", step: "any" },
  { key: "envelopeYLowUm", label: "Envelope Y lower", unit: "µm", step: "any" },
  { key: "envelopeYHighUm", label: "Envelope Y upper", unit: "µm", step: "any" },
  { key: "envelopeZLowUm", label: "Envelope Z lower", unit: "µm", step: "any" },
  { key: "envelopeZHighUm", label: "Envelope Z upper", unit: "µm", step: "any" },
  { key: "maximumFieldElapsedS", label: "Maximum field elapsed", unit: "s", step: "any" },
  { key: "maximumFieldZTravelUm", label: "Maximum field Z travel", unit: "µm", step: "any" },
  {
    key: "postMoveVerificationReserveS",
    label: "Post-move verification reserve",
    unit: "s",
    step: "any",
  },
];

const whiteFields = [
  { key: "whiteRangeLowUm", label: "WHITE search lower", unit: "µm", step: "any" },
  { key: "whiteRangeHighUm", label: "WHITE search upper", unit: "µm", step: "any" },
  { key: "whiteSearchTimeoutS", label: "WHITE search timeout", unit: "s", step: "any" },
  { key: "totalFocusBudgetS", label: "Total WHITE-to-R/G budget", unit: "s", step: "any" },
  {
    key: "approachAndRgReserveS",
    label: "Approach and R/G reserve",
    unit: "s",
    step: "any",
  },
  {
    key: "maximumWhiteLedDisagreementUm",
    label: "Maximum WHITE/R/G disagreement",
    unit: "µm",
    step: "any",
  },
];

const focusAccelerationDefaults = {
  unpredictedFocusMode: "selected_method",
  noTissueMode: "keep_z",
  allowExtrapolation: true,
  minimumPlanePoints: "4",
  maximumNeighbors: "12",
  maximumCandidateObservations: "256",
  neighborRadiusUm: "1200",
  maximumObservationAgeS: "1800",
  maximumExtrapolationUm: "500",
  maximumPredictionDeltaUm: "12",
  measurementOffsetUm: "-3",
  maximumFitResidualUm: "3",
  minimumFitInlierFraction: "0.75",
  maximumFitConditionNumber: "1000",
  maximumPlaneSlopeUmPerUm: "0.03",
  predictionErrorLowUm: "-4",
  predictionErrorHighUm: "4",
  envelopeXLowUm: "-50",
  envelopeXHighUm: "1000",
  envelopeYLowUm: "-1000",
  envelopeYHighUm: "50",
  envelopeZLowUm: "-50",
  envelopeZHighUm: "50",
  maximumFieldElapsedS: "180",
  maximumFieldZTravelUm: "200",
  postMoveVerificationReserveS: "30",
};

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function canonicalDisabledSettings(method = "openflexure", strategy = "smart_stack") {
  return {
    run: {
      autofocus_method: method,
      focus_strategy: strategy,
      surface: {
        enabled: false,
        minimum_plane_points: 4,
        maximum_neighbors: 12,
        maximum_candidate_observations: 256,
        neighbor_radius_um: null,
        allow_extrapolation: false,
        maximum_extrapolation_um: null,
        maximum_prediction_delta_um: null,
        measurement_offset_um: null,
        maximum_fit_residual_um: null,
        minimum_fit_inlier_fraction: null,
        maximum_fit_condition_number: null,
        maximum_plane_slope_um_per_um: null,
        maximum_observation_age_s: null,
      },
      unpredicted_focus_mode: "selected_method",
      white_search: null,
      no_tissue_mode: "keep_z",
      on_focus_failure: "pause",
      binding: null,
    },
    expected_prediction_error_range_um: null,
    maximum_field_elapsed_s: null,
    maximum_field_z_travel_um: null,
    post_move_verification_reserve_s: null,
    experiment_envelope: null,
  };
}

export default {
  name: "FocusScanSetupStatus",
  components: { CompactHelp },

  props: {
    mode: { type: String, default: "setup" },
    workflowName: { type: String, default: null },
    focus: { type: Object, default: null },
  },

  data() {
    return {
      mapFields,
      budgetFields,
      whiteFields,
      capability: null,
      savedSettings: null,
      savedDraft: null,
      draft: null,
      loading: false,
      applying: false,
      applyState: "idle",
      applyMessage: "",
      loadGeneration: 0,
      capabilityGeneration: 0,
    };
  },

  computed: {
    dirty() {
      return JSON.stringify(this.draft) !== JSON.stringify(this.savedDraft);
    },
    whiteControlsEnabled() {
      return this.draft?.enabled && this.draft.unpredictedFocusMode === "white_then_led";
    },
    bindingRows() {
      const binding = this.capability?.current_binding;
      if (!binding) return [];
      const axes = (binding.axis_scales || [])
        .map((axis) => `${axis.axis}: ${axis.units_per_mm} units/mm, sign ${axis.direction_sign}`)
        .join("; ");
      return [
        { label: "Stage controller ID", value: binding.stage_controller_id },
        { label: "Reference", value: `${binding.reference_id} (${binding.reference_status})` },
        { label: "Axis scales", value: axes },
        {
          label: "Camera-stage mapping",
          value: `${binding.camera_stage_mapping_id} (${binding.camera_stage_mapping_status})`,
        },
        { label: "Geometry ID", value: binding.geometry_id },
        {
          label: "R/G model",
          value: `${binding.rg_focus_model_id} (${binding.rg_focus_model_status})`,
        },
        {
          label: "RED flat-field",
          value: `${binding.red_flat_field_profile_id} (${binding.red_flat_field_status})`,
        },
        {
          label: "GREEN flat-field",
          value: `${binding.green_flat_field_profile_id} (${binding.green_flat_field_status})`,
        },
        {
          label: "Approach",
          value: `${binding.approach_profile_id} (${binding.approach_status})`,
        },
        {
          label: "Approach parameters",
          value: `preload ${binding.approach_parameters?.preload_um} µm, sign ${binding.approach_parameters?.approach_sign}`,
        },
      ];
    },
    runtimeLabel() {
      if (!this.focus) return "Unavailable";
      const prefix =
        { completed: "Complete · ", stopped: "Stopped · " }[this.focus.scan_state] || "";
      return `${prefix}${this.formatValue(this.focus.outcome)}`;
    },
    runtimeLabelClass() {
      if (["failed", "unknown"].includes(this.focus?.outcome)) return "uk-label-danger";
      if (this.focus?.outcome === "cancelled") return "uk-label-warning";
      return ["focused", "captured"].includes(this.focus?.outcome) ? "uk-label-success" : "";
    },
  },

  watch: {
    workflowName: {
      immediate: true,
      handler(name) {
        if (this.mode === "setup" && name) this.loadSetup();
      },
    },
  },

  methods: {
    formatValue(value) {
      return String(value ?? "unknown").replaceAll("_", " ");
    },
    formatPath(path) {
      return (
        {
          mapped_rg: "Mapped R/G",
          selected_method: "Selected method",
          white_then_rg: "WHITE → R/G",
        }[path] || this.formatValue(path)
      );
    },
    normaliseSettings(value, capability = this.capability) {
      const base = canonicalDisabledSettings(
        capability?.autofocus_method || value?.run?.autofocus_method,
        capability?.focus_strategy || value?.run?.focus_strategy,
      );
      const input = value || {};
      return {
        ...base,
        ...input,
        run: {
          ...base.run,
          ...(input.run || {}),
          surface: { ...base.run.surface, ...(input.run?.surface || {}) },
        },
      };
    },
    valueAt(value, index = null) {
      const selected = index === null ? value : value?.[index];
      return selected === null || selected === undefined ? "" : String(selected);
    },
    draftFromSettings(settings) {
      const value = this.normaliseSettings(settings);
      const surface = value.run.surface;
      const white = value.run.white_search;
      const envelope = value.experiment_envelope;
      return {
        enabled: surface.enabled,
        unpredictedFocusMode: value.run.unpredicted_focus_mode,
        noTissueMode: value.run.no_tissue_mode,
        allowExtrapolation: surface.allow_extrapolation,
        minimumPlanePoints: this.valueAt(surface.minimum_plane_points),
        maximumNeighbors: this.valueAt(surface.maximum_neighbors),
        maximumCandidateObservations: this.valueAt(surface.maximum_candidate_observations),
        neighborRadiusUm: this.valueAt(surface.neighbor_radius_um),
        maximumObservationAgeS: this.valueAt(surface.maximum_observation_age_s),
        maximumExtrapolationUm: this.valueAt(surface.maximum_extrapolation_um),
        maximumPredictionDeltaUm: this.valueAt(surface.maximum_prediction_delta_um),
        measurementOffsetUm: this.valueAt(surface.measurement_offset_um),
        maximumFitResidualUm: this.valueAt(surface.maximum_fit_residual_um),
        minimumFitInlierFraction: this.valueAt(surface.minimum_fit_inlier_fraction),
        maximumFitConditionNumber: this.valueAt(surface.maximum_fit_condition_number),
        maximumPlaneSlopeUmPerUm: this.valueAt(surface.maximum_plane_slope_um_per_um),
        predictionErrorLowUm: this.valueAt(value.expected_prediction_error_range_um, 0),
        predictionErrorHighUm: this.valueAt(value.expected_prediction_error_range_um, 1),
        envelopeXLowUm: this.valueAt(envelope?.x_um, 0),
        envelopeXHighUm: this.valueAt(envelope?.x_um, 1),
        envelopeYLowUm: this.valueAt(envelope?.y_um, 0),
        envelopeYHighUm: this.valueAt(envelope?.y_um, 1),
        envelopeZLowUm: this.valueAt(envelope?.z_um, 0),
        envelopeZHighUm: this.valueAt(envelope?.z_um, 1),
        maximumFieldElapsedS: this.valueAt(value.maximum_field_elapsed_s),
        maximumFieldZTravelUm: this.valueAt(value.maximum_field_z_travel_um),
        postMoveVerificationReserveS: this.valueAt(value.post_move_verification_reserve_s),
        whiteRangeLowUm: this.valueAt(white?.search_z_range_um, 0),
        whiteRangeHighUm: this.valueAt(white?.search_z_range_um, 1),
        whiteSearchTimeoutS: this.valueAt(white?.white_search_timeout_s),
        totalFocusBudgetS: this.valueAt(white?.total_focus_budget_s),
        approachAndRgReserveS: this.valueAt(white?.approach_and_rg_reserve_s),
        maximumWhiteLedDisagreementUm: this.valueAt(white?.maximum_white_led_disagreement_um),
      };
    },
    async loadSetup() {
      if (!this.workflowName) return;
      const generation = ++this.loadGeneration;
      const capabilityGeneration = ++this.capabilityGeneration;
      const workflow = this.workflowName;
      this.loading = true;
      try {
        const [settings, capability] = await Promise.all([
          this.readThingProperty(workflow, "focus_scan", true),
          this.getThingEndpoint(workflow, "focus_scan_capability"),
        ]);
        if (generation !== this.loadGeneration) return;
        if (!settings) throw new Error("Saved focus setup could not be read; refresh to retry.");
        const loadedCapability = capability || {
          supported: false,
          available: false,
          reasons: ["Focus capability is unavailable"],
          autofocus_method: null,
          focus_strategy: null,
          current_binding: null,
          saved_binding_current: null,
          saved_binding_mismatched_fields: [],
        };
        if (capabilityGeneration === this.capabilityGeneration) this.capability = loadedCapability;
        this.savedSettings = this.normaliseSettings(settings, this.capability);
        this.savedDraft = this.draftFromSettings(this.savedSettings);
        this.draft = clone(this.savedDraft);
        this.applyState = "idle";
        this.applyMessage = "";
      } catch (error) {
        if (generation !== this.loadGeneration) return;
        this.draft = null;
        this.applyState = "failed";
        this.applyMessage = String(error.message || error);
      } finally {
        if (generation === this.loadGeneration) this.loading = false;
      }
    },
    async refreshCapability() {
      const generation = this.loadGeneration;
      const capabilityGeneration = ++this.capabilityGeneration;
      const workflow = this.workflowName;
      const capability = await this.getThingEndpoint(workflow, "focus_scan_capability");
      if (
        generation === this.loadGeneration &&
        workflow === this.workflowName &&
        capabilityGeneration === this.capabilityGeneration
      ) {
        this.capability = capability || {
          ...this.capability,
          available: false,
          current_binding: null,
          reasons: ["Current focus capability could not be refreshed"],
        };
      }
    },
    enabledChanged() {
      if (!this.draft.enabled) {
        this.draft.unpredictedFocusMode = "selected_method";
        return;
      }
      const requiredValues = [
        "neighborRadiusUm",
        "maximumObservationAgeS",
        "maximumExtrapolationUm",
        "maximumPredictionDeltaUm",
        "measurementOffsetUm",
        "maximumFitResidualUm",
        "minimumFitInlierFraction",
        "maximumFitConditionNumber",
        "maximumPlaneSlopeUmPerUm",
        "predictionErrorLowUm",
        "predictionErrorHighUm",
        "envelopeXLowUm",
        "envelopeXHighUm",
        "envelopeYLowUm",
        "envelopeYHighUm",
        "envelopeZLowUm",
        "envelopeZHighUm",
        "maximumFieldElapsedS",
        "maximumFieldZTravelUm",
        "postMoveVerificationReserveS",
      ];
      const missingRequiredValue = requiredValues.some((key) => {
        const value = this.draft[key];
        return value === null || value === undefined || String(value).trim() === "";
      });
      if (missingRequiredValue) Object.assign(this.draft, focusAccelerationDefaults);
    },
    numberFromDraft(key, label, { required = false, integer = false } = {}) {
      const raw = this.draft[key];
      if (raw === null || raw === undefined || (typeof raw === "string" && !raw.trim())) {
        if (required) throw new Error(`${label} is required`);
        return null;
      }
      if (typeof raw === "boolean") throw new Error(`${label} must be a number`);
      const value = Number(raw);
      if (!Number.isFinite(value) || (integer && !Number.isInteger(value))) {
        throw new Error(`${label} must be a finite ${integer ? "integer" : "number"}`);
      }
      return value;
    },
    rangeFromDraft(lowKey, highKey, label, required) {
      const low = this.numberFromDraft(lowKey, `${label} lower`, { required });
      const high = this.numberFromDraft(highKey, `${label} upper`, { required });
      if ((low === null) !== (high === null)) throw new Error(`${label} requires both bounds`);
      return low === null ? null : [low, high];
    },
    buildPayload({ forceDisabled = false } = {}) {
      const enabled = !forceDisabled && this.draft.enabled;
      if (enabled && (!this.capability?.available || !this.capability.current_binding)) {
        throw new Error("Current workflow selection and physical binding cannot enable prediction");
      }
      const surface = { enabled, allow_extrapolation: this.draft.allowExtrapolation };
      for (const field of mapFields) {
        if (["predictionErrorLowUm", "predictionErrorHighUm"].includes(field.key)) continue;
        const apiName = {
          minimumPlanePoints: "minimum_plane_points",
          maximumNeighbors: "maximum_neighbors",
          maximumCandidateObservations: "maximum_candidate_observations",
          neighborRadiusUm: "neighbor_radius_um",
          maximumObservationAgeS: "maximum_observation_age_s",
          maximumExtrapolationUm: "maximum_extrapolation_um",
          maximumPredictionDeltaUm: "maximum_prediction_delta_um",
          measurementOffsetUm: "measurement_offset_um",
          maximumFitResidualUm: "maximum_fit_residual_um",
          minimumFitInlierFraction: "minimum_fit_inlier_fraction",
          maximumFitConditionNumber: "maximum_fit_condition_number",
          maximumPlaneSlopeUmPerUm: "maximum_plane_slope_um_per_um",
        }[field.key];
        const required = enabled || field.integer;
        const value = this.numberFromDraft(field.key, field.label, {
          required,
          integer: field.integer,
        });
        if (value !== null) surface[apiName] = value;
      }
      const predictionRange = this.rangeFromDraft(
        "predictionErrorLowUm",
        "predictionErrorHighUm",
        "Expected prediction error",
        enabled,
      );
      const envelopeRanges = {
        x_um: this.rangeFromDraft("envelopeXLowUm", "envelopeXHighUm", "Envelope X", enabled),
        y_um: this.rangeFromDraft("envelopeYLowUm", "envelopeYHighUm", "Envelope Y", enabled),
        z_um: this.rangeFromDraft("envelopeZLowUm", "envelopeZHighUm", "Envelope Z", enabled),
      };
      const envelopePresent = Object.values(envelopeRanges).some((value) => value !== null);
      if (envelopePresent && Object.values(envelopeRanges).some((value) => value === null)) {
        throw new Error("Experiment envelope requires X, Y and Z ranges");
      }
      const useWhite = enabled && this.draft.unpredictedFocusMode === "white_then_led";
      const whiteSearch = useWhite
        ? {
            search_z_range_um: this.rangeFromDraft(
              "whiteRangeLowUm",
              "whiteRangeHighUm",
              "WHITE search Z",
              true,
            ),
            white_search_timeout_s: this.numberFromDraft(
              "whiteSearchTimeoutS",
              "WHITE search timeout",
              { required: true },
            ),
            total_focus_budget_s: this.numberFromDraft("totalFocusBudgetS", "Total focus budget", {
              required: true,
            }),
            approach_and_rg_reserve_s: this.numberFromDraft(
              "approachAndRgReserveS",
              "Approach and R/G reserve",
              { required: true },
            ),
            maximum_white_led_disagreement_um: this.numberFromDraft(
              "maximumWhiteLedDisagreementUm",
              "Maximum WHITE/R/G disagreement",
              { required: true },
            ),
            maximum_white_searches_per_field: 1,
          }
        : null;
      return {
        run: {
          autofocus_method: this.capability.autofocus_method,
          focus_strategy: this.capability.focus_strategy,
          surface,
          unpredicted_focus_mode: enabled ? this.draft.unpredictedFocusMode : "selected_method",
          white_search: whiteSearch,
          no_tissue_mode: this.draft.noTissueMode,
          on_focus_failure: "pause",
          binding: enabled ? clone(this.capability.current_binding) : null,
        },
        expected_prediction_error_range_um: predictionRange,
        maximum_field_elapsed_s: this.numberFromDraft(
          "maximumFieldElapsedS",
          "Maximum field elapsed",
          { required: enabled },
        ),
        maximum_field_z_travel_um: this.numberFromDraft(
          "maximumFieldZTravelUm",
          "Maximum field Z travel",
          { required: enabled },
        ),
        post_move_verification_reserve_s: this.numberFromDraft(
          "postMoveVerificationReserveS",
          "Post-move verification reserve",
          { required: enabled },
        ),
        experiment_envelope: envelopePresent ? envelopeRanges : null,
      };
    },
    async persist(payload, message) {
      if (this.applying) return;
      const workflow = this.workflowName;
      const generation = this.loadGeneration;
      const draftAtWrite = JSON.stringify(this.draft);
      const current = () => generation === this.loadGeneration && workflow === this.workflowName;
      let writeCompleted = false;
      this.applying = true;
      this.applyState = "saving";
      this.applyMessage = "Saving…";
      try {
        await this.writeThingProperty(workflow, "focus_scan", payload);
        writeCompleted = true;
        const readback = await this.readThingProperty(workflow, "focus_scan", true);
        if (!current()) return;
        if (!readback) throw new Error("Focus setup saved but readback was unavailable");
        const expected = this.normaliseSettings(payload);
        const actual = this.normaliseSettings(readback);
        if (JSON.stringify(actual) !== JSON.stringify(expected)) {
          throw new Error("Focus setup readback did not match the atomic payload");
        }
        this.savedSettings = actual;
        this.savedDraft = this.draftFromSettings(this.savedSettings);
        if (JSON.stringify(this.draft) === draftAtWrite) this.draft = clone(this.savedDraft);
        await this.refreshCapability();
        if (!current()) return;
        this.applyState = "saved";
        this.applyMessage = message;
      } catch (error) {
        if (!current()) return;
        this.applyState = "failed";
        const prefix = writeCompleted
          ? "Save could not be confirmed; refresh before retrying"
          : "Save failed; refresh to check the stored value";
        this.applyMessage = `${prefix}: ${error.message || error}`;
        this.modalError(error);
      } finally {
        this.applying = false;
      }
    },
    async applyDraft() {
      try {
        await this.persist(this.buildPayload(), "Saved and read back for the next scan.");
      } catch (error) {
        this.applyState = "failed";
        this.applyMessage = `Not saved: ${error.message || error}`;
        this.modalError(error);
      }
    },
    async disableNextScan() {
      const payload = clone(this.savedSettings);
      payload.run.autofocus_method = this.capability.autofocus_method;
      payload.run.focus_strategy = this.capability.focus_strategy;
      payload.run.surface.enabled = false;
      payload.run.unpredicted_focus_mode = "selected_method";
      payload.run.white_search = null;
      payload.run.binding = null;
      await this.persist(payload, "Focus prediction is off for the next scan.");
    },
    async resetNextScan() {
      const payload = canonicalDisabledSettings(
        this.capability?.autofocus_method,
        this.capability?.focus_strategy,
      );
      await this.persist(
        payload,
        "Next-scan focus setup was reset; active scan data was untouched.",
      );
    },
  },
};
</script>

<style scoped>
.focus-runtime,
.focus-details {
  border: 1px solid #d8d8d8;
  border-radius: 4px;
  padding: 10px 12px;
}

.focus-form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px 14px;
}

.focus-form-grid label {
  font-size: 0.85rem;
}

.focus-checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
}

.focus-readonly,
.focus-summary-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 6px 14px;
}

.focus-readonly div,
.focus-summary-list div {
  min-width: 0;
}

.focus-readonly dt,
.focus-summary-list dt {
  color: inherit;
  opacity: 0.8;
  font-size: 0.8rem;
}

.focus-readonly dd,
.focus-summary-list dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-weight: 600;
}

.focus-details summary {
  cursor: pointer;
  font-weight: 600;
}

.focus-counters {
  display: flex;
  flex-wrap: wrap;
  gap: 5px 12px;
}
</style>
