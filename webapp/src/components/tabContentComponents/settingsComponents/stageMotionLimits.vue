<template>
  <section v-if="available" class="uk-margin-top">
    <h4>Software movement limits</h4>
    <compact-help
      label="Where limits apply"
      text="Shared by Control, autofocus, calibrations and scans. Values are relative to local zero. Saving does not move the stage or change zero."
    />
    <p v-if="error" class="uk-text-danger" role="alert">{{ error }}</p>
    <p v-if="!draft && !error">Reading movement limits…</p>
    <template v-if="draft && hardware">
      <fieldset
        v-for="axis in axes"
        :key="axis"
        class="uk-fieldset uk-margin"
        :disabled="saving || !hardware.axes[axis].enabled"
      >
        <legend class="uk-legend">{{ axis.toUpperCase() }}</legend>
        <label>
          <input
            v-model="draft[axis].travel_limit_enabled"
            class="uk-checkbox"
            type="checkbox"
            :data-limit="axis + '-travel'"
            @change="edited"
          />
          Limit travel relative to zero
        </label>
        <div class="uk-grid-small uk-child-width-1-2 uk-margin-small-top" uk-grid>
          <label>
            Minimum (µm)
            <input
              v-model.number="draft[axis].min_um"
              class="uk-input uk-form-small"
              type="number"
              :data-limit="axis + '-min'"
              :step="1000 / hardware.axes[axis].units_per_mm"
              :disabled="!draft[axis].travel_limit_enabled"
              @input="edited"
            />
          </label>
          <label>
            Maximum (µm)
            <input
              v-model.number="draft[axis].max_um"
              class="uk-input uk-form-small"
              type="number"
              :data-limit="axis + '-max'"
              :step="1000 / hardware.axes[axis].units_per_mm"
              :disabled="!draft[axis].travel_limit_enabled"
              @input="edited"
            />
          </label>
        </div>
        <label class="uk-display-block uk-margin-small-top">
          <input
            v-model="draft[axis].single_move_limit_enabled"
            class="uk-checkbox"
            type="checkbox"
            :data-limit="axis + '-single'"
            @change="edited"
          />
          Limit one move
        </label>
        <label class="uk-display-block uk-margin-small-top">
          Maximum single move (µm)
          <input
            v-model.number="draft[axis].max_move_um"
            class="uk-input uk-form-small"
            type="number"
            :data-limit="axis + '-step'"
            :min="1000 / hardware.axes[axis].units_per_mm"
            :step="1000 / hardware.axes[axis].units_per_mm"
            :disabled="!draft[axis].single_move_limit_enabled"
            @input="edited"
          />
        </label>
      </fieldset>
      <compact-help
        label="Hardware limits still apply"
        text="Klipper bounds, valid zero, motion completion, speed, acceleration and timeouts remain active when software limits are off. These checks cannot detect an objective collision; take particular care with Z."
        tone="warning"
      />
      <label v-if="hasDisabled" class="uk-display-block uk-margin uk-text-warning">
        <input v-model="confirmed" class="uk-checkbox" type="checkbox" :disabled="saving" />
        I confirm disabling the selected software limits and will check the available travel.
      </label>
      <p v-if="!valid" class="uk-text-danger" role="alert">
        Enter finite values. Travel must contain zero, minimum must be below maximum, and the
        single-move value must be positive and at least one stage unit.
      </p>
      <action-button
        thing="stage"
        action="set_motion_limits"
        submit-label="Apply movement limits"
        :submit-data="payload"
        :is-disabled="!valid || (hasDisabled && !confirmed)"
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
import { eventBus } from "@/eventBus.js";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";

export default {
  name: "StageMotionLimits",
  components: { ActionButton, CompactHelp },
  data: () => ({
    axes: ["x", "y", "z"],
    draft: null,
    original: null,
    hardware: null,
    saving: false,
    confirmed: false,
    error: "",
    message: "",
    requestId: 0,
  }),
  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    available() {
      return Boolean(this.thingDescription("stage")?.actions?.set_motion_limits);
    },
    hasDisabled() {
      return this.axes.some(
        (axis) =>
          this.hardware?.axes?.[axis]?.enabled &&
          (!this.draft?.[axis]?.travel_limit_enabled ||
            !this.draft?.[axis]?.single_move_limit_enabled),
      );
    },
    valid() {
      if (!this.draft || !this.hardware) return false;
      return this.axes.every((axis) => {
        if (!this.hardware.axes[axis].enabled) return true;
        const value = this.draft[axis];
        return (
          [value.min_um, value.max_um, value.max_move_um].every(Number.isFinite) &&
          value.min_um <= 0 &&
          value.max_um >= 0 &&
          value.min_um < value.max_um &&
          value.max_move_um >= 1000 / this.hardware.axes[axis].units_per_mm
        );
      });
    },
    payload() {
      if (!this.valid) return {};
      return {
        limits: Object.fromEntries(
          this.axes.map((axis) => {
            if (!this.hardware.axes[axis].enabled) return [axis, this.original[axis]];
            const value = this.draft[axis];
            return [
              axis,
              {
                travel_limit_enabled: value.travel_limit_enabled,
                min_mm: value.min_um / 1000,
                max_mm: value.max_um / 1000,
                single_move_limit_enabled: value.single_move_limit_enabled,
                max_move_mm: value.max_move_um / 1000,
              },
            ];
          }),
        ),
        confirm_disable: this.hasDisabled && this.confirmed,
      };
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
      this.confirmed = false;
      this.message = "";
      this.error = "";
    },
    async load() {
      const id = ++this.requestId;
      this.draft = null;
      this.hardware = null;
      this.error = "";
      this.confirmed = false;
      if (!this.available) return;
      try {
        const [limits, hardware] = await Promise.all([
          this.readThingProperty("stage", "motion_limits", true),
          this.readThingProperty("stage", "hardware_settings", true),
        ]);
        if (id !== this.requestId) return;
        if (!this.axes.every((axis) => limits?.[axis] && hardware?.axes?.[axis]))
          throw new Error("Incomplete movement limits");
        this.original = limits;
        this.hardware = hardware;
        this.draft = Object.fromEntries(
          this.axes.map((axis) => [
            axis,
            {
              ...limits[axis],
              min_um: limits[axis].min_mm == null ? null : limits[axis].min_mm * 1000,
              max_um: limits[axis].max_mm == null ? null : limits[axis].max_mm * 1000,
              max_move_um: limits[axis].max_move_mm * 1000,
            },
          ]),
        );
      } catch {
        if (id === this.requestId) this.error = "Cannot read movement limits. Nothing was changed.";
      }
    },
    async onSaved() {
      await this.load();
      if (!this.error) this.message = "Movement limits saved on the microscope.";
      eventBus.emit("stageMotionLimitsChanged");
    },
    onError(error) {
      this.message = "";
      this.error =
        (typeof error === "string" ? error : error?.message) ||
        "Limits were not confirmed. Reload to check the current values.";
    },
  },
};
</script>
