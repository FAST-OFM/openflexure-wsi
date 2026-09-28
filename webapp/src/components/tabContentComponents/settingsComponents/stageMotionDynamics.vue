<template>
  <section v-if="available" class="uk-margin-top">
    <h4>Automatic motion</h4>
    <compact-help
      label="Scan timing"
      text="Used by scans, calibration and autofocus. Manual Control keeps its zero-settle profile. Saving these values does not move the stage or change zero."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <p v-if="!draft && !error">Reading motion settings…</p>
    <template v-if="draft && hardware">
      <div class="uk-overflow-auto">
        <table class="uk-table uk-table-small uk-table-divider motion-table">
          <thead>
            <tr>
              <th>Axis</th>
              <th>Speed<br />(mm/s)</th>
              <th>Acceleration<br />(mm/s²)</th>
              <th>Settle<br />(ms)</th>
              <th>Timeout<br />(s)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="axis in axes" :key="axis">
              <th>{{ axis.toUpperCase() }}</th>
              <td>
                <input
                  v-model.number="draft[axis].speed_mm_s"
                  class="uk-input uk-form-small"
                  type="number"
                  min="0.001"
                  step="0.01"
                  :disabled="saving || !hardware.axes[axis].enabled"
                  :data-motion="axis + '-speed'"
                  @input="edited"
                />
              </td>
              <td>
                <input
                  v-model.number="draft[axis].accel_mm_s2"
                  class="uk-input uk-form-small"
                  type="number"
                  min="0.001"
                  step="0.1"
                  :disabled="saving || !hardware.axes[axis].enabled"
                  :data-motion="axis + '-accel'"
                  @input="edited"
                />
              </td>
              <td>
                <input
                  v-model.number="draft[axis].settle_ms"
                  class="uk-input uk-form-small"
                  type="number"
                  min="0"
                  max="5000"
                  step="10"
                  :disabled="saving || !hardware.axes[axis].enabled"
                  :data-motion="axis + '-settle'"
                  @input="edited"
                />
              </td>
              <td>
                <input
                  v-model.number="draft[axis].move_timeout_s"
                  class="uk-input uk-form-small"
                  type="number"
                  min="0.1"
                  max="120"
                  step="0.1"
                  :disabled="saving || !hardware.axes[axis].enabled"
                  :data-motion="axis + '-timeout'"
                  @input="edited"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!valid" class="uk-text-danger" role="alert">
        Enter positive speed, acceleration and timeout values. Settle must be 0–5000 ms.
      </p>
      <action-button
        thing="stage"
        action="set_motion_dynamics"
        submit-label="Apply motion settings"
        :submit-data="payload"
        :is-disabled="!valid"
        :can-terminate="false"
        @submit="saving = true"
        @task-started="saving = true"
        @finished="saving = false"
        @response="onSaved"
        @error="onError"
      />
      <p v-if="message" role="status">{{ message }}</p>
    </template>
  </section>
</template>

<script>
import ActionButton from "@/components/labThingsComponents/actionButton.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import { eventBus } from "@/eventBus.js";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";

export default {
  name: "StageMotionDynamics",
  components: { ActionButton, CompactHelp },
  data: () => ({
    axes: ["x", "y", "z"],
    draft: null,
    hardware: null,
    saving: false,
    error: "",
    message: "",
    requestId: 0,
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      return Boolean(this.thingDescription("stage")?.actions?.set_motion_dynamics);
    },
    valid() {
      if (!this.draft || !this.hardware) return false;
      return this.axes.every((axis) => {
        if (!this.hardware.axes[axis].enabled) return true;
        const value = this.draft[axis];
        return (
          [value.speed_mm_s, value.accel_mm_s2, value.move_timeout_s].every(
            (item) => Number.isFinite(item) && item > 0,
          ) &&
          Number.isFinite(value.settle_ms) &&
          value.settle_ms >= 0 &&
          value.settle_ms <= 5000 &&
          value.move_timeout_s <= 120
        );
      });
    },
    payload() {
      return this.valid ? { dynamics: this.draft } : {};
    },
  },
  watch: {
    available: { immediate: true, handler: "load" },
    baseUri: "load",
  },
  beforeUnmount() {
    this.requestId++;
  },
  methods: {
    edited() {
      this.error = "";
      this.message = "";
    },
    async load() {
      const id = ++this.requestId;
      this.draft = null;
      this.hardware = null;
      this.error = "";
      if (!this.available) return;
      try {
        const [dynamics, hardware] = await Promise.all([
          this.readThingProperty("stage", "motion_dynamics", true),
          this.readThingProperty("stage", "hardware_settings", true),
        ]);
        if (id !== this.requestId) return;
        if (!this.axes.every((axis) => dynamics?.[axis] && hardware?.axes?.[axis]))
          throw new Error("Incomplete motion settings");
        this.draft = structuredClone(dynamics);
        this.hardware = hardware;
      } catch {
        if (id === this.requestId) this.error = "Cannot read motion settings. Nothing was changed.";
      }
    },
    async onSaved() {
      await this.load();
      if (!this.error) this.message = "Motion settings saved on the microscope.";
      eventBus.emit("stageMotionDynamicsChanged");
    },
    onError(error) {
      this.message = "";
      this.error =
        (typeof error === "string" ? error : error?.message) ||
        "Motion settings were not confirmed. Reload to check the current values.";
    },
  },
};
</script>

<style scoped lang="less">
.motion-table input {
  min-width: 5.5rem;
}
</style>
