<template>
  <section v-if="available" class="uk-margin-top rg-simultaneous-settings">
    <h4>Simultaneous R+G autofocus</h4>
    <compact-help
      class="uk-margin-small-bottom"
      label="Experimental one-frame method"
      text="Uses one RAW exposure with RED and GREEN on together. Its flat-field, focus curve and enable switch are independent; separate R/G remains the default fallback."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <dl class="uk-description-list uk-margin-small">
      <dt>RAW flat-field</dt>
      <dd :class="flatValid ? 'uk-text-success' : 'uk-text-warning'">
        {{ statusText(flatStatus) }}
      </dd>
      <dt>Focus curve</dt>
      <dd :class="focusValid ? 'uk-text-success' : 'uk-text-warning'">
        {{ statusText(focusStatus) }}
      </dd>
      <dt>Selection</dt>
      <dd :class="enabled ? 'uk-text-success' : 'uk-text-meta'">
        {{ enabled ? "Available in focus selectors" : "Disabled" }}
      </dd>
    </dl>

    <details class="uk-margin-small">
      <summary>RAW flat-field calibration</summary>
      <p class="uk-text-small">Remove the slide. The stage does not move and WHITE is restored.</p>
      <label class="uk-display-block uk-margin-small">
        <input v-model="emptyPrepared" type="checkbox" class="uk-checkbox" :disabled="running" />
        Empty field is visible and microscope control is exclusive.
      </label>
      <action-button
        thing="rg_simultaneous"
        action="calibrate"
        submit-label="Calibrate simultaneous R+G flat-field"
        :submit-data="{ prepared: emptyPrepared }"
        :is-disabled="running || !emptyPrepared || !flatReadiness?.ready"
        :can-terminate="true"
        :modal-progress="true"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
    </details>

    <details class="uk-margin-small">
      <summary>Tissue focus curve</summary>
      <p class="uk-text-small">
        Start from focused textured tissue with a current zero and clear Z travel. The grid returns
        to its verified start before replacing the curve.
      </p>
      <label class="uk-display-block uk-margin-small">
        <input v-model="tissuePrepared" type="checkbox" class="uk-checkbox" :disabled="running" />
        Focused tissue, Z clearance and exclusive control are confirmed.
      </label>
      <div class="uk-grid-small uk-child-width-1-2@m" uk-grid>
        <action-button
          thing="rg_simultaneous"
          action="calibrate_focus"
          submit-label="Calibrate simultaneous R+G focus"
          :submit-data="{ prepared: tissuePrepared }"
          :is-disabled="running || !tissuePrepared || !focusReadiness?.ready"
          :can-terminate="true"
          :modal-progress="true"
          @task-started="started"
          @completed="completed"
          @finished="finished"
          @error="onError"
        />
        <action-button
          thing="rg_simultaneous"
          action="measure_focus"
          submit-label="Measure one mixed frame"
          :submit-data="{ prepared: tissuePrepared }"
          :is-disabled="running || !tissuePrepared || !focusReadiness?.ready"
          :can-terminate="true"
          :modal-progress="true"
          @task-started="started"
          @completed="completed"
          @finished="finished"
          @error="onError"
        />
      </div>
    </details>

    <details v-if="focusParameters" class="uk-margin-small">
      <summary>Focus processing area</summary>
      <compact-help
        class="uk-margin-small-bottom"
        label="Central RAW crop"
        text="Only autofocus processing is cropped. Full scan images and the saved flat-field remain unchanged. Saving requires a new simultaneous focus curve."
      />
      <div class="uk-grid-small uk-child-width-1-2@s" uk-grid>
        <label v-for="(label, index) in focusRoiLabels" :key="label">
          {{ label }}, RAW-plane px
          <input
            v-model.number="focusParameters.focus_plane_roi[index]"
            class="uk-input"
            type="number"
            min="0"
            max="1400"
            step="1"
            :disabled="running"
          />
        </label>
        <label>
          Maximum tissue windows
          <input
            v-model.number="focusParameters.core.maximum_patch_count"
            class="uk-input"
            type="number"
            min="6"
            max="48"
            step="1"
            :disabled="running"
          />
        </label>
      </div>
      <action-button
        thing="rg_simultaneous"
        action="set_focus_parameters"
        submit-label="Save focus processing area"
        :submit-data="{ parameters: focusParameters }"
        :is-disabled="running"
        :button-primary="false"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
    </details>

    <div class="uk-margin-small-top">
      <action-button
        thing="rg_simultaneous"
        action="set_enabled"
        :submit-label="enabled ? 'Disable simultaneous R+G' : 'Enable simultaneous R+G'"
        :submit-data="{ enabled: !enabled }"
        :is-disabled="running || (!enabled && (!flatValid || !focusValid))"
        :button-primary="false"
        @task-started="started"
        @completed="completed"
        @finished="finished"
        @error="onError"
      />
    </div>
    <button
      type="button"
      class="uk-button uk-button-text uk-margin-small-top"
      :disabled="running"
      @click="load"
    >
      Refresh status
    </button>
  </section>
</template>

<script>
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import ActionButton from "@/components/labThingsComponents/actionButton.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "RGSimultaneousSettings",
  components: { ActionButton, CompactHelp },
  data: () => ({
    flatStatus: null,
    focusStatus: null,
    flatReadiness: null,
    focusReadiness: null,
    focusParameters: null,
    focusRoiLabels: ["X", "Y", "Width", "Height"],
    emptyPrepared: false,
    tissuePrepared: false,
    running: false,
    error: "",
    epoch: 0,
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      const actions = this.thingDescription("rg_simultaneous")?.actions;
      return Boolean(
        actions?.calibrate &&
        actions?.calibrate_focus &&
        actions?.set_focus_parameters &&
        actions?.set_enabled,
      );
    },
    flatValid() {
      return this.flatStatus?.status === "valid";
    },
    focusValid() {
      return this.focusStatus?.status === "valid";
    },
    enabled() {
      return this.flatStatus?.enabled === true && this.focusStatus?.enabled === true;
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
      this.error = "";
      this.emptyPrepared = false;
      this.tissuePrepared = false;
      if (!this.available) return;
      try {
        const [flatStatus, focusStatus, flatReadiness, focusReadiness, focusParameters] =
          await Promise.all([
            this.readThingProperty("rg_simultaneous", "status", true),
            this.readThingProperty("rg_simultaneous", "focus_status", true),
            this.readThingProperty("rg_simultaneous", "readiness", true),
            this.readThingProperty("rg_simultaneous", "focus_readiness", true),
            this.readThingProperty("rg_simultaneous", "focus_parameters", true),
          ]);
        if (id === this.epoch) {
          this.flatStatus = flatStatus || null;
          this.focusStatus = focusStatus || null;
          this.flatReadiness = flatReadiness || null;
          this.focusReadiness = focusReadiness || null;
          this.focusParameters = focusParameters
            ? JSON.parse(JSON.stringify(focusParameters))
            : null;
        }
      } catch (error) {
        if (id === this.epoch) this.onError(error);
      }
    },
    started() {
      this.running = true;
      this.error = "";
    },
    completed() {
      this.emptyPrepared = false;
      this.tissuePrepared = false;
      void this.load();
    },
    finished() {
      this.running = false;
    },
    onError(error) {
      this.running = false;
      this.emptyPrepared = false;
      this.tissuePrepared = false;
      this.error = error?.message || String(error);
    },
    statusText(status) {
      if (!status) return "Unavailable";
      return `${status.status}${status.reason ? ` — ${status.reason}` : ""}`;
    },
  },
};
</script>
