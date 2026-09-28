<template>
  <section v-if="available" ref="panel" class="uk-margin-bottom">
    <h4>Z backlash calibration</h4>
    <compact-help
      label="Before calibration"
      text="Use WHITE light and focused textured tissue inside the marked region. Check Z clearance and do not use other movement controls during measurement."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <template v-if="draft">
      <div class="z-preview">
        <mini-stream-display stream-id="z-backlash-settings" />
        <div class="z-roi" :style="roiStyle" aria-label="Selected tissue region"></div>
      </div>
      <fieldset class="uk-fieldset uk-margin" :disabled="running || saving">
        <div class="uk-grid-small uk-child-width-1-2@s" uk-grid>
          <label v-for="field in basicFields" :key="field.key">
            {{ field.label }}
            <input
              v-model.number="draft[field.key]"
              class="uk-input uk-form-small"
              type="number"
              :data-z="field.key"
              :min="field.min"
              :step="field.step"
              @input="edited"
            />
          </label>
        </div>
        <label class="uk-display-block uk-margin-small-top">
          Final Z approach
          <select
            v-model.number="draft.preferred_final_approach_sign"
            class="uk-select"
            @change="edited"
          >
            <option :value="1">Positive Z</option>
            <option :value="-1">Negative Z</option>
          </select>
        </label>
        <details class="uk-margin">
          <summary>Measurement and quality settings</summary>
          <div class="uk-grid-small uk-child-width-1-2@s uk-margin-small-top" uk-grid>
            <label v-for="field in advancedFields" :key="field.key">
              {{ field.label }}
              <input
                v-model.number="draft[field.key]"
                class="uk-input uk-form-small"
                type="number"
                :data-z="field.key"
                step="any"
                min="0"
                @input="edited"
              />
            </label>
            <label v-for="field in roiFields" :key="field.key">
              {{ field.label }} (% of frame)
              <input
                :value="draft.roi[field.key] * 100"
                class="uk-input uk-form-small"
                type="number"
                min="0"
                max="100"
                step="1"
                :data-roi="field.key"
                @input="setRoi(field.key, $event.target.value)"
              />
            </label>
            <label>
              Mechanical setup label
              <input
                v-model="draft.mechanics_id"
                class="uk-input"
                maxlength="100"
                @input="edited"
              />
            </label>
            <label>
              Optical setup label
              <input v-model="draft.optics_id" class="uk-input" maxlength="100" @input="edited" />
            </label>
          </div>
          <p class="uk-text-meta">
            Change setup labels after mechanical or optical changes. Speed, acceleration and
            settling come from the shared stage profile; this procedure does not raise limits.
          </p>
        </details>
      </fieldset>
      <p v-if="!valid" class="uk-text-danger" role="alert">
        Check the numeric values, sampling grid and tissue region.
      </p>
      <action-button
        v-if="dirty"
        thing="z_backlash"
        action="set_parameters"
        submit-label="Save calibration parameters"
        :submit-data="{ parameters: draft }"
        :is-disabled="!valid || running"
        :can-terminate="false"
        @submit="saving = true"
        @task-started="saving = true"
        @finished="saving = false"
        @response="load"
        @error="onError"
      />
      <p v-if="!readiness?.ready" class="uk-text-warning" role="status">
        {{ readiness?.reason || "Checking microscope readiness…" }}
      </p>
      <label class="uk-display-block uk-margin">
        <input
          v-model="prepared"
          type="checkbox"
          class="uk-checkbox"
          data-z="prepared"
          :disabled="running || saving"
        />
        Tissue is focused in the marked region, Z travel is clear, and I have exclusive control.
      </label>
      <action-button
        thing="z_backlash"
        action="calibrate"
        submit-label="Calibrate Z backlash"
        :submit-data="{ prepared }"
        :is-disabled="!valid || dirty || !prepared || !readiness?.ready || saving"
        :can-terminate="true"
        :modal-progress="true"
        :stream-with-modal="true"
        @submit="running = true"
        @task-started="running = true"
        @finished="running = false"
        @response="onCompleted"
        @error="onError"
      />
      <p v-if="running" role="status">
        {{ progress?.phase || "Starting" }} · {{ progress?.frames || 0 }} frames
      </p>
      <compact-help
        label="Where the stage stops"
        text="Success returns to the start. Cancel or failure stops at the last confirmed position and keeps the previous calibration."
      />
    </template>
    <div class="uk-margin-top" data-z="result">
      <h5>Saved calibration</h5>
      <p :class="{ 'uk-text-warning': status?.status === 'incompatible' }" role="status">
        {{ statusLabel }}<span v-if="status?.reason"> — {{ status.reason }}</span>
      </p>
      <template v-if="result">
        <dl class="uk-description-list">
          <dt>Date</dt>
          <dd>{{ result.created_at }}</dd>
          <dt>Z backlash</dt>
          <dd>{{ number(result.estimate?.backlash_um) }} µm</dd>
          <dt>Uncertainty</dt>
          <dd>{{ number(result.estimate?.uncertainty_um) }} µm</dd>
          <dt>Minimum focus-curve quality (0–1)</dt>
          <dd>{{ number(result.estimate?.quality_score) }}</dd>
          <dt>Verified preload</dt>
          <dd>{{ number(result.preload_um) }} µm</dd>
          <dt>Maximum residual</dt>
          <dd>{{ number(result.maximum_residual_um) }} µm</dd>
        </dl>
        <button class="uk-button uk-button-default" type="button" @click="downloadResult">
          Download result
        </button>
      </template>
    </div>
  </section>
</template>

<script>
import { useIntersectionObserver } from "@vueuse/core";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import ActionButton from "@/components/labThingsComponents/actionButton.vue";
import MiniStreamDisplay from "@/components/genericComponents/miniStreamDisplay.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "ZBacklashSettings",
  components: { ActionButton, MiniStreamDisplay, CompactHelp },
  data: () => ({
    draft: null,
    original: "",
    prepared: false,
    running: false,
    saving: false,
    error: "",
    readiness: null,
    status: null,
    result: null,
    progress: null,
    epoch: 0,
    visible: false,
    timer: null,
    stopObserver: null,
    basicFields: [
      { key: "span_um", label: "Total Z sweep (µm)", min: 0, step: 2 },
      { key: "step_um", label: "Sampling step (µm)", min: 0, step: 1 },
      { key: "preload_um", label: "Preparation preload (µm)", min: 0, step: 1 },
      { key: "cycles", label: "Automatic ABBA cycles", min: 2, step: 1 },
    ],
    advancedFields: [
      { key: "validation_half_range_um", label: "Verification half-range (µm)" },
      { key: "maximum_uncertainty_um", label: "Maximum uncertainty (µm)" },
      { key: "preload_margin_um", label: "Preload margin (µm)" },
      { key: "maximum_residual_um", label: "Maximum verification residual (µm)" },
      { key: "maximum_drift_um", label: "Maximum drift per cycle (µm)" },
      { key: "minimum_prominence", label: "Minimum focus prominence (0–1)" },
      { key: "minimum_texture", label: "Minimum reference texture" },
      { key: "minimum_signal", label: "Minimum signal (0–1)" },
      { key: "maximum_saturation_fraction", label: "Maximum saturated fraction (0–1)" },
      { key: "frame_timeout_s", label: "Capture timeout (s)" },
      { key: "timeout_s", label: "Total timeout (s)" },
      { key: "maximum_frames", label: "Maximum frame count" },
    ],
    roiFields: [
      { key: "x", label: "Region left" },
      { key: "y", label: "Region top" },
      { key: "width", label: "Region width" },
      { key: "height", label: "Region height" },
    ],
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      return Boolean(this.thingDescription("z_backlash")?.actions?.calibrate);
    },
    dirty() {
      return this.draft && JSON.stringify(this.draft) !== this.original;
    },
    valid() {
      if (!this.draft) return false;
      const p = this.draft;
      if (
        ![...this.basicFields, ...this.advancedFields].every(
          ({ key }) =>
            Number.isFinite(p[key]) &&
            (key === "maximum_saturation_fraction" ? p[key] >= 0 : p[key] > 0),
        )
      )
        return false;
      const intervals = p.span_um / p.step_um;
      const verification = p.validation_half_range_um / p.step_um;
      const roi = p.roi;
      return (
        p.step_um > 0 &&
        p.preload_um >= p.step_um &&
        Number.isInteger(intervals) &&
        intervals >= 4 &&
        intervals % 2 === 0 &&
        Number.isInteger(verification) &&
        verification >= 2 &&
        p.validation_half_range_um <= p.span_um / 2 &&
        Number.isInteger(p.cycles) &&
        p.cycles >= 2 &&
        p.cycles <= 10 &&
        [1, -1].includes(p.preferred_final_approach_sign) &&
        p.minimum_prominence < 1 &&
        p.minimum_signal < 1 &&
        p.maximum_saturation_fraction < 1 &&
        p.frame_timeout_s <= 30 &&
        p.timeout_s <= 3600 &&
        Number.isInteger(p.maximum_frames) &&
        p.maximum_frames >= 10 &&
        p.maximum_frames <= 5000 &&
        1 + p.cycles * 4 * (intervals + 1) + 2 * (2 * verification + 1) <= p.maximum_frames &&
        this.roiFields.every(({ key }) => Number.isFinite(roi[key])) &&
        roi.x >= 0 &&
        roi.y >= 0 &&
        roi.width > 0 &&
        roi.height > 0 &&
        roi.x + roi.width <= 1 &&
        roi.y + roi.height <= 1 &&
        Boolean(p.mechanics_id?.trim()) &&
        p.mechanics_id.length <= 100 &&
        Boolean(p.optics_id?.trim()) &&
        p.optics_id.length <= 100
      );
    },
    roiStyle() {
      const r = this.draft?.roi;
      return r
        ? {
            left: r.x * 100 + "%",
            top: r.y * 100 + "%",
            width: r.width * 100 + "%",
            height: r.height * 100 + "%",
          }
        : {};
    },
    statusLabel() {
      return (
        { valid: "Valid", incompatible: "Incompatible", not_calibrated: "Not calibrated" }[
          this.status?.status
        ] || "Status unavailable"
      );
    },
  },
  watch: {
    available: { immediate: true, handler: "load" },
    baseUri: "load",
  },
  mounted() {
    const { stop } = useIntersectionObserver(this.$refs.panel, ([{ isIntersecting }]) => {
      this.visible = isIntersecting;
      clearTimeout(this.timer);
      if (this.visible) this.poll();
    });
    this.stopObserver = stop;
  },
  beforeUnmount() {
    this.epoch++;
    this.visible = false;
    clearTimeout(this.timer);
    this.stopObserver?.();
  },
  methods: {
    number(value) {
      return Number.isFinite(value) ? value.toFixed(2) : "—";
    },
    edited() {
      this.prepared = false;
      this.error = "";
    },
    setRoi(key, value) {
      this.draft.roi[key] = value === "" ? NaN : Number(value) / 100;
      this.edited();
    },
    async refresh(id) {
      const names = ["readiness", "calibration_status", "last_calibration", "progress"];
      const values = await Promise.all(
        names.map((name) => this.readThingProperty("z_backlash", name, true)),
      );
      if (id !== this.epoch) return;
      [this.readiness, this.status, this.result, this.progress] = values;
    },
    async load() {
      const id = ++this.epoch;
      this.draft = null;
      this.prepared = false;
      this.error = "";
      this.readiness = null;
      this.status = null;
      this.result = null;
      if (!this.available) return;
      try {
        const parameters = await this.readThingProperty("z_backlash", "parameters", true);
        if (id !== this.epoch) return;
        this.draft = JSON.parse(JSON.stringify(parameters));
        this.original = JSON.stringify(this.draft);
        await this.refresh(id);
      } catch (error) {
        if (id === this.epoch) this.onError(error);
      }
    },
    async poll() {
      const id = this.epoch;
      try {
        if (this.available) await this.refresh(id);
      } catch {
        if (id === this.epoch) this.readiness = { ready: false, reason: "Microscope unavailable" };
      }
      if (this.visible) this.timer = setTimeout(() => this.poll(), 2000);
    },
    async onCompleted() {
      this.running = false;
      this.prepared = false;
      await this.load();
    },
    onError(error) {
      this.error = error?.message || String(error);
      this.prepared = false;
    },
    downloadResult() {
      if (!this.result) return;
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(this.result, null, 2)], {
          type: "application/json",
        }),
      );
      const link = document.createElement("a");
      link.href = url;
      link.download = "z-backlash-calibration.json";
      link.click();
      URL.revokeObjectURL(url);
    },
  },
};
</script>

<style scoped>
.z-preview {
  position: relative;
  max-width: 360px;
  margin: auto;
}

.z-roi {
  position: absolute;
  border: 2px solid currentcolor;
  pointer-events: none;
  box-sizing: border-box;
}
</style>
