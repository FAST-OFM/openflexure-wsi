<template>
  <section v-if="available" class="uk-margin-top rg-focus-settings">
    <h4>Tissue R/G autofocus</h4>
    <compact-help
      class="uk-margin-small-bottom"
      label="How R/G focus works"
      text="Uses a fresh WHITE tissue field, separate RED/GREEN captures and the saved signed Z model. This calibration action never falls back to standard autofocus."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <p :class="statusClass" role="status">
      {{ statusLabel }}<span v-if="status?.reason"> — {{ status.reason }}</span>
    </p>
    <div class="rg-focus-contract" data-rg-focus="contract">
      <p :class="readinessStatusClass" role="status">
        {{ readinessLabel }}
        <span v-if="readiness"> — {{ readiness.policy_state }}</span>
      </p>
      <action-button
        v-if="contractActionAvailable"
        thing="rg_focus"
        action="apply_demo_focus_contract"
        submit-label="Apply JPEG demo contract"
        :submit-data="{}"
        :is-disabled="running"
        :button-primary="false"
        @submit="contractStarted"
        @task-started="contractStarted"
        @completed="contractApplied"
        @finished="finished"
        @error="onError"
      />
      <details v-if="readiness" class="uk-margin-small">
        <summary>JPEG contract details</summary>
        <ul class="uk-list uk-list-divider uk-text-small">
          <li>
            Domain: {{ readiness.expected_policy?.measurement_domain }} · policy
            {{ shortHash(readiness.expected_policy_sha256) }}
          </li>
          <li>
            JPEG {{ dimensions(readiness.geometry?.expected?.source_image_size) }} · ROI
            {{ csv(readiness.geometry?.expected?.processing_roi) }} · working
            {{ dimensions(readiness.geometry?.expected?.image_size) }}
          </li>
          <li>
            Patches
            {{ readiness.expected_policy?.core?.patch_size_px }} px /
            {{ readiness.expected_policy?.core?.patch_stride_px }} px · minimum
            {{ readiness.expected_policy?.core?.minimum_patch_count }} · signal ≥
            {{ readiness.expected_policy?.core?.minimum_patch_signal }} DN · saturation
            {{ readiness.expected_policy?.core?.jpeg8_saturation_level }} DN
          </li>
          <li>
            Z grid {{ csv(readiness.expected_parameters?.calibration_parameters?.z_offsets_um) }} µm
            · holdout/returns/stationary ≤2 µm · autofocus
            {{ readiness.expected_parameters?.control_parameters?.maximum_iterations }} iterations,
            ≤{{ readiness.expected_parameters?.control_parameters?.maximum_total_correction_um }} µm
          </li>
        </ul>
        <p v-if="mismatchRows.length" class="uk-text-warning uk-text-small">Blocking checks:</p>
        <ul v-if="mismatchRows.length" class="uk-list uk-text-small">
          <li v-for="row in mismatchRows" :key="`${row.code}:${row.path}`">
            {{ row.code }} <span class="uk-text-meta">({{ row.path }})</span>
          </li>
          <li v-if="readiness.mismatches.length > mismatchRows.length" class="uk-text-meta">
            +{{ readiness.mismatches.length - mismatchRows.length }} more
          </li>
        </ul>
      </details>
    </div>
    <details class="uk-margin-small">
      <summary>Measurement stages and calibration</summary>
      <ol>
        <li>Capture a fresh WHITE field and select fixed valid tissue windows.</li>
        <li>Capture corrected RED/GREEN planes and apply strict shift QC.</li>
        <li>Project the signed 2D shift through the saved calibration model.</li>
        <li>After any bounded Z correction, capture an independent verification pair.</li>
      </ol>
      <p class="uk-text-meta">
        Calibration reserves space for the final approach inside its full ±24 µm path, performs the
        predefined fit, holdout and two-sided approach checks, then returns to its commanded start
        on success. A failed check is saved as candidate and cannot move Z.
      </p>
    </details>
    <details class="uk-margin-small" data-rg-focus="approach-settings">
      <summary>Z final approach</summary>
      <p class="uk-text-small">
        Calibration and autofocus share this approach. Changing it requires a new accepted
        calibration.
      </p>
      <label class="uk-display-block">
        Take-up distance, µm
        <input
          v-model.number="approach.preload_um"
          class="uk-input"
          type="number"
          min="1"
          max="16"
          step="1"
          :disabled="running"
        />
      </label>
      <label class="uk-display-block">
        Final direction
        <select v-model.number="approach.approach_sign" class="uk-select" :disabled="running">
          <option :value="1">Increasing Z (+)</option>
          <option :value="-1">Decreasing Z (−)</option>
        </select>
      </label>
      <action-button
        thing="rg_focus"
        action="set_approach_parameters"
        submit-label="Save Z approach"
        :submit-data="{ parameters: approach }"
        :is-disabled="running || !approachLoaded"
        @task-started="started"
        @completed="load"
        @finished="finished"
        @error="onError"
      />
    </details>
    <label class="uk-display-block uk-margin-small">
      <input
        v-model="prepared"
        type="checkbox"
        class="uk-checkbox"
        data-rg-focus="prepared"
        :disabled="running"
      />
      Textured tissue is visible, Z travel is clear, and I have exclusive microscope control.
    </label>
    <div class="uk-grid-small uk-child-width-1-3@m" uk-grid>
      <action-button
        thing="rg_focus"
        action="calibrate"
        submit-label="Calibrate tissue R/G focus"
        :submit-data="{ prepared }"
        :is-disabled="!prepared || !readiness?.calibration_ready || running"
        :can-terminate="true"
        :modal-progress="true"
        @submit="started"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
      <action-button
        thing="rg_focus"
        action="measure"
        submit-label="Measure R/G focus signal"
        :submit-data="{ prepared }"
        :is-disabled="!prepared || !readiness?.measure_ready || running"
        :can-terminate="true"
        :modal-progress="true"
        @submit="started"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
      <action-button
        thing="rg_focus"
        action="autofocus"
        submit-label="Run tissue R/G autofocus"
        :submit-data="{ prepared }"
        :is-disabled="!prepared || !readiness?.autofocus_ready || running"
        :can-terminate="true"
        :modal-progress="true"
        @submit="started"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
    </div>
    <p v-if="running" role="status">R/G action running; use the action button to cancel.</p>
    <div v-if="result" class="uk-margin-top" data-rg-focus="result">
      <p>
        Result: <strong>{{ result.status }}</strong
        ><span v-if="lastDecision"> — {{ lastDecision.reason }}</span>
      </p>
      <img
        v-if="overlayUrl"
        :src="overlayUrl"
        alt="Fresh WHITE tissue windows used by the latest R/G focus measurement"
        class="rg-focus-overlay"
      />
    </div>
    <button
      type="button"
      class="uk-button uk-button-text uk-margin-small-top"
      :disabled="running"
      @click="load"
    >
      Refresh calibration status
    </button>
  </section>
</template>

<script>
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import ActionButton from "@/components/labThingsComponents/actionButton.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "RGFocusSettings",
  components: { ActionButton, CompactHelp },
  data: () => ({
    status: null,
    readiness: null,
    result: null,
    prepared: false,
    running: false,
    error: "",
    epoch: 0,
    approach: { preload_um: 8, approach_sign: 1 },
    approachLoaded: false,
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      const actions = this.thingDescription("rg_focus")?.actions;
      return Boolean(actions?.calibrate && actions?.measure && actions?.autofocus);
    },
    profileValid() {
      return this.status?.status === "valid";
    },
    contractActionAvailable() {
      return Boolean(this.thingDescription("rg_focus")?.actions?.apply_demo_focus_contract);
    },
    statusLabel() {
      return (
        {
          valid: "Calibration valid",
          candidate: "Calibration candidate only",
          incompatible: "Calibration incompatible",
          not_calibrated: "Not calibrated",
        }[this.status?.status] || "Calibration status unavailable"
      );
    },
    statusClass() {
      return this.profileValid ? "uk-text-success" : "uk-text-warning";
    },
    readinessLabel() {
      if (!this.readiness) return "Checking JPEG focus readiness";
      if (!this.readiness.calibration_ready) return "JPEG focus setup blocked";
      if (!this.readiness.commissioning_match) return "Ready for stationary calibration";
      if (!this.readiness.autofocus_ready) return "Measurement ready; accepted model required";
      return "Ready for R/G autofocus";
    },
    readinessStatusClass() {
      return this.readiness?.autofocus_ready ? "uk-text-success" : "uk-text-warning";
    },
    mismatchRows() {
      return (this.readiness?.mismatches || []).slice(0, 5);
    },
    lastDecision() {
      return this.result?.iterations?.at(-1)?.decision;
    },
    overlayUrl() {
      if (!this.result?.data_path) return "";
      const iteration = Math.max(
        1,
        this.result.iterations?.length || this.result.captures?.length || 1,
      );
      const name = `iteration-${String(iteration).padStart(2, "0")}-overlay.png`;
      return `${this.baseUri}/data/rg_focus/${encodeURIComponent(this.result.data_path)}/${name}`;
    },
  },
  watch: {
    available: { immediate: true, handler: "load" },
    baseUri: "load",
  },
  beforeUnmount() {
    this.epoch++;
  },
  methods: {
    async load() {
      const id = ++this.epoch;
      this.status = null;
      this.readiness = null;
      this.error = "";
      this.approachLoaded = false;
      this.prepared = false;
      if (!this.available) return;
      try {
        const [status, readiness, approach] = await Promise.all([
          this.readThingProperty("rg_focus", "calibration_status", true),
          this.readThingProperty("rg_focus", "readiness", true),
          this.readThingProperty("rg_focus", "approach_parameters", true),
        ]);
        if (id === this.epoch) {
          this.status = status || null;
          this.readiness = readiness || null;
          this.approach = approach;
          this.approachLoaded = true;
        }
      } catch (error) {
        if (id === this.epoch) this.onError(error);
      }
    },
    started() {
      this.running = true;
      this.result = null;
      this.error = "";
    },
    contractStarted() {
      this.started();
      this.prepared = false;
      this.readiness = null;
    },
    contractApplied() {
      this.prepared = false;
      void this.load();
    },
    completed(output) {
      this.result = output;
      this.prepared = false;
      void this.load();
    },
    finished() {
      this.running = false;
    },
    onError(error) {
      this.running = false;
      this.prepared = false;
      this.error = error?.message || String(error);
    },
    shortHash(value) {
      return typeof value === "string" ? value.slice(0, 12) : "unavailable";
    },
    csv(value) {
      return Array.isArray(value) ? value.join(", ") : "unavailable";
    },
    dimensions(value) {
      return Array.isArray(value) ? value.join("×") : "unavailable";
    },
  },
};
</script>

<style scoped>
.rg-focus-overlay {
  display: block;
  max-width: min(100%, 700px);
  max-height: 500px;
  object-fit: contain;
}

.rg-focus-contract {
  max-width: 700px;
}
</style>
