<template>
  <section v-if="available" class="uk-margin-top rg-flat-field">
    <h4>RED / GREEN flat-field</h4>
    <compact-help
      class="uk-margin-small-bottom"
      label="Before calibration"
      text="Remove the slide. This calibrates processed JPEG8 R/G images without stage movement. WHITE calibration and the normal colour preview are unchanged."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <p
      v-if="readiness && !readiness.ready && !running && !observedKey"
      class="uk-text-warning"
      role="status"
    >
      {{ readiness.reason }}
    </p>
    <section v-if="draft" class="uk-margin-small" data-rg="jpeg-profile">
      <p role="status" :class="jpegPresetSaved ? 'uk-text-meta' : 'uk-text-warning'">
        {{
          jpegPresetSaved
            ? "Processed JPEG8 profile saved."
            : "Save the explicit JPEG8 preset before RED/GREEN capture or calibration. Historical RAW maps remain stored, but are incompatible."
        }}
      </p>
      <p class="uk-text-meta">
        Common RED/GREEN processing ROI (JPEG pixels).
        {{
          imageSize
            ? `Full source: ${imageSize[0]} × ${imageSize[1]} px.`
            : "Current JPEG dimensions unavailable."
        }}
        The full WHITE scan field is unchanged.
      </p>
      <fieldset :disabled="running || observedKey || saving" class="uk-fieldset rg-roi">
        <label v-for="(label, index) in ['X', 'Y', 'Width', 'Height']" :key="label">
          {{ label }} (px)
          <input
            v-model.number="draft.processing_roi[index]"
            type="number"
            step="1"
            :min="index < 2 ? 0 : 16"
            :max="imageSize?.[index % 2]"
            class="uk-input uk-form-small"
            :data-rg-roi="index"
            @input="edited"
          />
        </label>
      </fieldset>
      <p
        v-if="!savedParameters?.processing_roi && imageSize?.[0] === 1014 && imageSize?.[1] === 760"
        class="uk-text-meta"
      >
        Initial ROI suggestion only; review the values and save explicitly. No ROI has been saved
        yet.
      </p>
      <p v-if="!validRoi" class="uk-text-warning uk-text-small">
        Enter a whole-pixel ROI of at least 16 × 16 inside the current JPEG image.
      </p>
      <template v-if="!jpegPresetSaved">
        <p class="uk-text-meta">
          The JPEG8 preset sets minimum signal to 8 DN and maximum OFF signal to 32 DN (0–255).
          Existing frame counts, timeouts and quality limits are kept. No capture or light change.
        </p>
        <action-button
          thing="rg_flat_field"
          action="use_jpeg_preset"
          submit-label="Save JPEG8 preset and ROI"
          :submit-data="{ processing_roi: draft.processing_roi }"
          :is-disabled="running || Boolean(observedKey) || saving || !validRoi"
          :can-terminate="false"
          @submit="saving = true"
          @finished="saving = false"
          @response="parametersSaved"
          @error="onError"
        />
      </template>
      <action-button
        v-else-if="dirty"
        thing="rg_flat_field"
        action="set_parameters"
        submit-label="Save flat-field settings"
        :submit-data="{ parameters: draft }"
        :is-disabled="running || Boolean(observedKey) || saving || !validDraft"
        :can-terminate="false"
        @submit="saving = true"
        @finished="saving = false"
        @response="parametersSaved"
        @error="onError"
      />
    </section>
    <label class="uk-display-block uk-margin-small">
      <input
        v-model="prepared"
        type="checkbox"
        class="uk-checkbox"
        data-rg="prepared"
        :disabled="running || observedKey || saving"
      />
      The field is empty and I am not using other microscope controls.
    </label>
    <action-button
      thing="rg_flat_field"
      action="calibrate"
      submit-label="Calibrate RED + GREEN"
      :submit-data="{ prepared, modes: ['red', 'green'] }"
      :is-disabled="blockedFor('both')"
      :modal-progress="true"
      :stream-with-modal="true"
      @update:task-running="observed.both = $event"
      @submit="started('both')"
      @finished="finished('both')"
      @response="load"
      @error="onError"
    />
    <div v-for="mode in modes" :key="mode" class="uk-margin-top" :data-rg="mode">
      <div class="rg-heading">
        <strong>{{ mode.toUpperCase() }}</strong>
        <span role="status">{{ statusLabel(mode) }}</span>
      </div>
      <p
        v-if="['incompatible', 'validation_failed', 'disabled'].includes(statuses?.[mode]?.status)"
        class="uk-text-warning uk-text-small"
      >
        {{ statuses[mode].reason }}
      </p>
      <p v-if="profiles?.[mode]" class="uk-text-meta">
        {{ date(profiles[mode].created_at) }} · variation
        {{ variation(profiles[mode].validation?.before?.cv) }} →
        {{ variation(profiles[mode].validation?.after?.cv) }}
      </p>
      <action-button
        thing="rg_flat_field"
        action="calibrate"
        :submit-label="'Calibrate ' + mode.toUpperCase()"
        :submit-data="{ prepared, modes: [mode] }"
        :is-disabled="blockedFor(mode)"
        :modal-progress="true"
        :stream-with-modal="true"
        :button-primary="false"
        @update:task-running="observed[mode] = $event"
        @submit="started(mode)"
        @finished="finished(mode)"
        @response="load"
        @error="onError"
      />
      <div v-if="profiles?.[mode]" class="uk-margin-small rg-map-controls">
        <action-button
          thing="rg_flat_field"
          action="set_enabled"
          :submit-label="
            (statuses?.[mode]?.status === 'disabled' ? 'Enable ' : 'Disable ') +
            mode.toUpperCase() +
            ' correction'
          "
          :submit-data="{ mode, enabled: statuses?.[mode]?.status === 'disabled' }"
          :is-disabled="running || Boolean(observedKey) || saving"
          :can-terminate="false"
          :button-primary="false"
          @update:task-running="observed['enable-' + mode] = $event"
          @submit="started('enable-' + mode)"
          @finished="finished('enable-' + mode)"
          @response="load"
          @error="onError"
        />
        <action-button
          thing="rg_flat_field"
          action="reset_profile"
          :submit-label="'Reset ' + mode.toUpperCase() + ' map'"
          :submit-data="{ mode }"
          :requires-confirmation="true"
          :confirmation-message="
            'Reset the active ' +
            mode.toUpperCase() +
            ' map? Historical files and the other colour are kept.'
          "
          :is-disabled="running || Boolean(observedKey) || saving"
          :can-terminate="false"
          :button-primary="false"
          @update:task-running="observed['reset-' + mode] = $event"
          @submit="started('reset-' + mode)"
          @finished="finished('reset-' + mode)"
          @response="load"
          @error="onError"
        />
      </div>
      <p v-if="profiles?.[mode] && !jpegProfile(mode)" class="uk-text-meta">
        Historical RAW/incompatible-domain map retained. Its preview is not a JPEG8 calibration.
      </p>
      <details v-if="jpegProfile(mode)" class="uk-margin-small">
        <summary>Saved JPEG8 ROI before / after</summary>
        <img
          :src="previewUrl(mode)"
          :alt="mode.toUpperCase() + ' validation: before left, after right'"
          loading="lazy"
        />
        <p class="uk-text-meta">
          Saved validation frames, common display scale. Check the current compatibility status
          above.
        </p>
      </details>
    </div>
    <action-button
      v-if="validModes.length"
      class="uk-margin-top"
      thing="rg_flat_field"
      action="validate"
      submit-label="Check saved maps"
      :submit-data="{ prepared, modes: validModes }"
      :is-disabled="blockedFor('validate')"
      :modal-progress="true"
      :stream-with-modal="true"
      :button-primary="false"
      @update:task-running="observed.validate = $event"
      @submit="started('validate')"
      @finished="finished('validate')"
      @response="validated"
      @error="onError"
    />
    <p v-if="validation" class="uk-text-success" role="status">
      Saved maps passed a separate fresh-frame check. WHITE restored.
    </p>
    <p v-if="brightnessNotice" class="uk-text-warning uk-text-small" role="status">
      Overall brightness changed by {{ brightnessNotice }}. Spatial correction passed; these maps do
      not stabilise the light source.
    </p>
    <details v-if="draft" class="uk-margin-top">
      <summary>Acquisition and quality settings</summary>
      <p v-if="!jpegPresetSaved" class="uk-text-meta">
        Stored legacy DN values are shown for reference only, not as JPEG8 settings. Save the JPEG8
        preset first.
      </p>
      <fieldset
        :disabled="running || observedKey || saving || !jpegPresetSaved"
        class="uk-fieldset uk-margin-small-top"
      >
        <label v-for="field in fields" :key="field.key" class="uk-display-block uk-margin-small">
          {{ field.label }}
          <input
            v-model.number="draft[field.key]"
            type="number"
            class="uk-input uk-form-small"
            :min="field.min"
            :max="field.max"
            :step="field.step || 'any'"
            :data-rg-setting="field.key"
            @input="edited"
          />
        </label>
        <label v-for="key in ['optics_id', 'illumination_id']" :key="key" class="uk-display-block">
          {{ key === "optics_id" ? "Optical setup label" : "Illumination setup label" }}
          <input
            v-model="draft[key]"
            class="uk-input uk-form-small"
            maxlength="100"
            @input="edited"
          />
        </label>
        <p class="uk-text-meta">
          Update labels when the physical setup changes. Exposure and gain use the fixed camera
          settings. Changing the camera or labels makes old maps incompatible.
        </p>
      </fieldset>
    </details>
    <p class="uk-text-meta">
      Cancel or failure keeps the previous maps. RED/GREEN are measured separately; the procedure
      restores WHITE before accepting a result.
    </p>
    <button
      type="button"
      class="uk-button uk-button-text"
      :disabled="running || observedKey || saving"
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
  name: "RGFlatFieldSettings",
  components: { ActionButton, CompactHelp },
  emits: ["actionStarted", "actionFinished"],
  data: () => ({
    modes: ["red", "green"],
    prepared: false,
    running: false,
    activeKey: null,
    observed: { both: false, red: false, green: false, validate: false },
    timer: null,
    saving: false,
    error: "",
    validation: null,
    readiness: null,
    statuses: null,
    profiles: null,
    lastChecks: {},
    loadedBaseUri: null,
    draft: null,
    savedParameters: null,
    cameraConfiguration: null,
    formEdited: false,
    original: "",
    epoch: 0,
    fields: [
      {
        key: "maximum_spatial_residual_fraction",
        label: "Maximum normalised spatial residual, P95 (0–1)",
        min: 0.001,
        max: 0.2,
      },
      { key: "dark_frames", label: "Dark frames", min: 2, max: 16, step: 1 },
      { key: "average_frames", label: "Flat-field frames", min: 2, max: 64, step: 1 },
      {
        key: "validation_frames",
        label: "Independent validation frames",
        min: 2,
        max: 16,
        step: 1,
      },
      { key: "frame_timeout_s", label: "Frame timeout (s)", min: 0.01, max: 10 },
      { key: "timeout_s", label: "Total timeout (s)", min: 1, max: 600 },
      { key: "smoothing_sigma_px", label: "Map smoothing (JPEG pixels)", min: 1, max: 64 },
      {
        key: "minimum_signal_dn",
        label: "Minimum signal above OFF (JPEG8 DN, 0–255)",
        min: 8,
        max: 255,
      },
      { key: "maximum_gain", label: "Maximum correction factor", min: 1, max: 10 },
      { key: "maximum_mask_fraction", label: "Maximum invalid fraction (0–1)", min: 0, max: 0.1 },
      {
        key: "maximum_saturation_fraction",
        label: "Maximum saturated fraction (0–1)",
        min: 0,
        max: 0.05,
      },
      {
        key: "saturation_level_fraction",
        label: "Saturation threshold (0–1 of JPEG8 range)",
        min: 0.9,
        max: 1,
      },
      {
        key: "maximum_dark_signal_dn",
        label: "Maximum absolute OFF signal (JPEG8 DN, 0–255)",
        min: 1,
        max: 255,
      },
      {
        key: "maximum_field_texture",
        label: "Maximum empty-field texture (0–1)",
        min: 0.001,
        max: 0.2,
      },
      {
        key: "maximum_residual_cv",
        label: "Maximum validation variation (0–1)",
        min: 0.001,
        max: 0.2,
      },
      {
        key: "maximum_drift_fraction",
        label: "Brightness-change warning threshold (0–1)",
        min: 0.001,
        max: 0.2,
      },
    ],
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      return Boolean(this.thingDescription("rg_flat_field")?.actions?.calibrate);
    },
    jpegPresetSaved() {
      return this.savedParameters?.measurement_domain === "processed-jpeg-rgb8";
    },
    imageSize() {
      const size = this.cameraConfiguration?.geometry?.image_size;
      return this.cameraConfiguration?.measurement_space === "processed-jpeg-rgb8" &&
        Array.isArray(size) &&
        size.length === 2 &&
        size.every((value) => Number.isInteger(value) && value >= 16)
        ? size
        : null;
    },
    validRoi() {
      const roi = this.draft?.processing_roi;
      return (
        this.imageSize &&
        Array.isArray(roi) &&
        roi.length === 4 &&
        roi.every(Number.isInteger) &&
        roi[0] >= 0 &&
        roi[1] >= 0 &&
        roi[2] >= 16 &&
        roi[3] >= 16 &&
        roi[0] + roi[2] <= this.imageSize[0] &&
        roi[1] + roi[3] <= this.imageSize[1]
      );
    },
    brightnessNotice() {
      const reports =
        this.validation?.validation ||
        Object.fromEntries(
          Object.entries(this.profiles || {}).map(([mode, profile]) => {
            const check = this.lastChecks?.[mode];
            return [
              mode,
              check?.profile_id === profile?.id && check?.status === "passed"
                ? check.quality
                : profile?.validation,
            ];
          }),
        );
      const changes = Object.values(reports).filter((report) => report?.brightness_change_notice);
      return changes.length
        ? (100 * Math.max(...changes.map((report) => report.brightness_drift_fraction))).toFixed(
            1,
          ) + "%"
        : "";
    },
    dirty() {
      return this.draft && JSON.stringify(this.draft) !== this.original;
    },
    validDraft() {
      return (
        this.draft &&
        this.jpegPresetSaved &&
        this.validRoi &&
        this.fields.every(
          ({ key, min, max, step }) =>
            Number.isFinite(this.draft[key]) &&
            this.draft[key] >= min &&
            this.draft[key] <= max &&
            (step !== 1 || Number.isInteger(this.draft[key])),
        ) &&
        ["optics_id", "illumination_id"].every((key) => this.draft[key]?.trim())
      );
    },
    observedKey() {
      return Object.keys(this.observed).find((key) => this.observed[key]) || null;
    },
    blocked() {
      return (
        !this.prepared ||
        !this.readiness?.ready ||
        this.running ||
        this.saving ||
        this.dirty ||
        !this.validDraft
      );
    },
    validModes() {
      return this.modes.filter(
        (mode) =>
          this.jpegProfile(mode) &&
          ["valid", "validation_failed"].includes(this.statuses?.[mode]?.status),
      );
    },
  },
  watch: {
    available: { immediate: true, handler: "load" },
    baseUri: "load",
  },
  mounted() {
    this.timer = setInterval(() => this.refreshStatus(), 5000);
  },
  beforeUnmount() {
    clearInterval(this.timer);
    this.epoch++;
  },
  methods: {
    statusLabel(mode) {
      return (
        {
          valid: "Valid",
          incompatible: "Incompatible",
          validation_failed: "Check failed",
          not_calibrated: "Not calibrated",
          disabled: "Disabled — map retained",
        }[this.statuses?.[mode]?.status] || "Status unavailable"
      );
    },
    date(value) {
      return value && Number.isFinite(new Date(value).getTime())
        ? new Date(value).toLocaleString()
        : "Date unavailable";
    },
    variation(values) {
      return Array.isArray(values) && values.length && values.every(Number.isFinite)
        ? (Math.max(...values) * 100).toFixed(2) + "%"
        : "—";
    },
    previewUrl(mode) {
      return this.baseUri + "/rg_flat_field/preview/" + mode + ".png?id=" + this.profiles[mode].id;
    },
    jpegProfile(mode) {
      return (
        this.profiles?.[mode]?.method === "phase_matched_processed_jpeg_measured_dark" &&
        Boolean(this.profiles[mode].id)
      );
    },
    edited() {
      this.formEdited = true;
      this.prepared = false;
      this.validation = null;
    },
    blockedFor(key) {
      const active = this.running ? this.activeKey : this.observedKey;
      return active ? active !== key : this.blocked;
    },
    async refreshStatus() {
      if (!this.available || this.running || this.saving || document.hidden) return;
      const id = this.epoch;
      try {
        const [readiness, statuses, profiles, lastChecks] = await Promise.all(
          ["readiness", "calibration_status", "profiles", "last_check"].map((name) =>
            this.readThingProperty("rg_flat_field", name, true),
          ),
        );
        if (id !== this.epoch) return;
        this.readiness = readiness;
        this.statuses = statuses;
        this.profiles = profiles;
        this.lastChecks = lastChecks || {};
      } catch {
        if (id === this.epoch) this.readiness = { ready: false, reason: "Microscope unavailable" };
      }
    },
    started(key) {
      this.activeKey = key;
      this.running = true;
      this.error = "";
      this.validation = null;
      this.$emit("actionStarted");
    },
    finished(key) {
      this.observed[key] = false;
      this.running = false;
      this.activeKey = null;
      this.prepared = false;
      this.$emit("actionFinished");
    },
    onError(error) {
      this.error = error?.message || String(error);
      this.prepared = false;
      this.validation = null;
    },
    async validated(result) {
      await this.load();
      this.validation = result.output || result;
    },
    async parametersSaved() {
      await this.load(true);
    },
    async load(force = false) {
      force = force === true;
      const id = ++this.epoch;
      this.prepared = false;
      this.error = "";
      this.validation = null;
      this.readiness = null;
      const changedConnection = this.loadedBaseUri !== this.baseUri;
      if (!this.available || changedConnection) {
        this.statuses = null;
        this.profiles = null;
        this.lastChecks = {};
        this.draft = null;
        this.savedParameters = null;
        this.cameraConfiguration = null;
        this.formEdited = false;
      }
      this.loadedBaseUri = this.baseUri;
      if (!this.available) return;
      try {
        const [values, configuration] = await Promise.all([
          Promise.all(
            ["parameters", "readiness", "calibration_status", "profiles", "last_check"].map(
              (name) => this.readThingProperty("rg_flat_field", name, true),
            ),
          ),
          this.readThingProperty("camera", "jpeg_measurement_configuration", true).catch(
            () => null,
          ),
        ]);
        if (id !== this.epoch) return;
        const [parameters, readiness, statuses, profiles, lastChecks] = values;
        this.cameraConfiguration = configuration;
        this.savedParameters = parameters;
        if (force || !this.formEdited || !this.dirty) {
          this.draft = JSON.parse(JSON.stringify(parameters));
          this.original = JSON.stringify(this.draft);
          if (!Array.isArray(this.draft.processing_roi) || this.draft.processing_roi.length !== 4) {
            this.draft.processing_roi =
              this.imageSize?.[0] === 1014 && this.imageSize?.[1] === 760
                ? [102, 0, 810, 760]
                : ["", "", "", ""];
          }
          this.formEdited = false;
        }
        this.readiness = readiness;
        this.statuses = statuses;
        this.profiles = profiles;
        this.lastChecks = lastChecks || {};
      } catch (error) {
        if (id === this.epoch) this.onError(error);
      }
    },
  },
};
</script>

<style scoped>
.rg-heading {
  display: flex;
  gap: 1rem;
  justify-content: space-between;
}

.rg-roi {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.rg-map-controls {
  display: grid;
  gap: 8px;
}
</style>
