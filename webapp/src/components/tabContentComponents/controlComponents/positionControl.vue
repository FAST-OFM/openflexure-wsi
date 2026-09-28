<!-- This is the main position component for the control tab.

Do not reuse this component until we remove global listeners for move.

This component shows the motor positions in a closed accordion along with the move
and zero position buttons. It also includes the d-pad.
-->
<template>
  <div>
    <template v-if="operatorControls && !focusOnly">
      <div class="uk-text-small uk-margin-small-bottom" role="status" aria-live="polite">
        Motors: {{ motorStatus }} · Zero: {{ controller?.reference_valid ? "set" : "not set" }}
      </div>
      <div class="motor-controls uk-margin-small-bottom">
        <action-button
          :key="motorsOff ? 'enable_motors' : 'disable_motors'"
          thing="stage"
          :action="motorsOff ? 'enable_motors' : 'disable_motors'"
          :submit-label="motorsOff ? 'Enable motors' : 'Disable motors'"
          :submit-data="motorsOff ? { exclusive_control: true } : { confirmed_release: true }"
          :can-terminate="false"
          :button-primary="false"
          :is-disabled="!controllerIdle || moveLock || !hardware?.allow_operator_controls"
          :requires-confirmation="true"
          :confirmation-message="
            motorsOff
              ? 'Enable XYZ motor holding power without moving? Do not control the stage from Fluidd at the same time.'
              : 'Release XYZ motors? Holding force and the local zero will be lost. The mechanism may shift under load. This is not an emergency stop.'
          "
          @submit="moveLock = true"
          @finished="moveComplete"
          @error="modalError"
        />
      </div>
      <action-button
        class="uk-margin-small-bottom"
        thing="stage"
        action="set_zero_position"
        submit-label="Set zero here"
        :submit-data="{
          confirmed_manual_zero: true,
          exclusive_control: true,
          initialise_controller: true,
        }"
        :can-terminate="false"
        :is-disabled="
          !controllerIdle ||
          moveLock ||
          !motorsEnabled ||
          !hardware?.allow_motion ||
          !hardware?.allow_operator_controls
        "
        :requires-confirmation="true"
        confirmation-message="Check the live image and stage clearance. Use this safe position as the reference for the configured travel limits? This does not move or home the stage. Confirm that no other client, including Fluidd, will move it."
        @submit="moveLock = true"
        @finished="moveComplete"
        @error="modalError"
      />
      <compact-help
        v-if="controller?.fault"
        class="uk-margin-small"
        label="Stage not ready"
        :text="controller.fault"
        tone="warning"
      />
    </template>
    <ul v-if="!focusOnly" uk-accordion="multiple: true">
      <li class="uk-closed">
        <a class="uk-accordion-title" href="#">Position</a>
        <div class="uk-accordion-content">
          <!-- Text boxes to set and view position -->
          <div class="input-and-buttons-container">
            <label v-for="(_v, key) in setPosition" :key="`setPosition_${key}`">
              {{ key.toUpperCase() }} ({{ operatorControls ? "µm" : "stage units" }})
              <input
                :value="displayCoordinate(key)"
                class="uk-form-small numeric-setting-line-input"
                type="number"
                :step="coordinateStep(key)"
                :disabled="operatorControls && (!canMove || !hardware?.axes?.[key]?.enabled)"
                @input="setCoordinate(key, $event.target.value)"
                @keyup.enter="startMoveTask"
              />
            </label>
            <icon-button icon="sync_alt" @click="updatePosition" />
          </div>
          <action-button
            ref="moveButton"
            class="uk-margin"
            thing="stage"
            :action="operatorControls ? 'move_manual' : 'move_absolute'"
            :submit-data="operatorControls ? { ...setPosition, relative: false } : setPosition"
            :submit-label="'Move'"
            :can-terminate="true"
            :poll-interval="0.05"
            :is-disabled="!coordinatesValid || (operatorControls && !canMove)"
            @submit="moveLock = true"
            @finished="moveComplete"
            @error="modalError"
          />
          <action-button
            v-if="!operatorControls"
            class="uk-margin"
            thing="stage"
            action="set_zero_position"
            submit-label="Set Home"
            :can-terminate="false"
            @finished="updatePosition"
            @error="modalError"
          />
          <action-button
            v-if="!operatorControls"
            class="uk-margin"
            thing="stage"
            action="move_to_origin"
            :submit-label="'Move Home'"
            :can-terminate="true"
            :poll-interval="0.05"
            :requires-confirmation="true"
            :confirmation-message="'<b>Remove your sample before continuing</b><br><br>Move the stage to (0, 0, 0)?'"
            @finished="moveComplete"
            @error="modalError"
          />
          <div class="uk-flex uk-flex-center uk-margin">
            <hr class="uk-divider-small" />
          </div>
        </div>
      </li>
    </ul>

    <stageControlButtons
      :title="stepSummary"
      :show-dpad="!focusOnly"
      :bounded="operatorControls"
      :disabled="operatorControls && !manualReady"
      :busy="operatorControls && moveLock"
      :axes-enabled="axisEnabled"
      :step-units="stepUnits"
      :move-step="boundedStep"
      :repeat-delay-ms="hardware?.ui_repeat_delay_ms ?? 100"
    />
    <p
      v-if="operatorControls && moveLock"
      class="uk-text-meta uk-text-center uk-margin-small"
      role="status"
      aria-live="polite"
    >
      Moving…
    </p>
    <compact-help
      v-if="operatorControls && hardware && !stepsValid"
      class="uk-margin-small uk-text-center"
      label="Check movement steps"
      text="One or more manual steps are outside the configured stage limits. Open Settings → Manual control."
      tone="warning"
    />
  </div>
</template>

<script>
import ActionButton from "../../labThingsComponents/actionButton.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import iconButton from "@/components/genericComponents/iconButton.vue";
import stageControlButtons from "./stageControlButtons.vue";
import { eventBus } from "../../../eventBus.js";
import modalMixin from "../../../mixins/modalMixins.js";
import { mapState } from "pinia";
import { useSettingsStore } from "@/stores/settings.js";
import { manualStageStep } from "@/js_utils/stageControlPreferences.js";

export default {
  name: "PaneControl",

  components: {
    ActionButton,
    CompactHelp,
    iconButton,
    stageControlButtons,
  },

  mixins: [modalMixin],

  props: {
    // Wizard controls share this implementation but never register movement listeners.
    focusOnly: { type: Boolean, default: false },
  },

  data: function () {
    return {
      setPosition: null,
      moveLock: false,
      jogging: false,
      controller: null,
      hardware: null,
      statusTimer: null,
      refreshing: false,
      unmounted: false,
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri", "stageNavigationStepsMm"]),
    operatorControls() {
      return Boolean(this.thingDescription("stage")?.actions?.enable_motors);
    },
    controllerIdle() {
      return this.controller?.state === "ready" && this.controller.motion_idle === true;
    },
    motorsEnabled() {
      return ["x", "y", "z"].every(
        (axis) => this.controller?.enabled?.["stepper_" + axis] === true,
      );
    },
    motorsOff() {
      return ["x", "y", "z"].every(
        (axis) => this.controller?.enabled?.["stepper_" + axis] === false,
      );
    },
    motorStatus() {
      if (!this.controller) return "unavailable";
      return this.motorsEnabled ? "on" : this.motorsOff ? "off" : "partly enabled";
    },
    canMove() {
      return this.manualReady && !this.moveLock;
    },
    manualReady() {
      return (
        this.controllerIdle &&
        this.controller?.reference_valid === true &&
        this.hardware?.allow_motion === true &&
        this.hardware?.allow_operator_controls === true &&
        Boolean(this.thingDescription("stage")?.actions?.move_manual) &&
        this.stepsValid
      );
    },
    axisEnabled() {
      return Object.fromEntries(
        ["x", "y", "z"].map((axis) => [
          axis,
          !this.operatorControls || this.hardware?.axes?.[axis]?.enabled === true,
        ]),
      );
    },
    manualSteps() {
      const saved = this.stageNavigationStepsMm[this.baseUri] ?? {};
      return Object.fromEntries(
        ["x", "y", "z"].map((axis) => [
          axis,
          manualStageStep(this.hardware?.axes?.[axis], saved[axis]),
        ]),
      );
    },
    stepsValid() {
      return (
        Boolean(this.hardware) &&
        (this.focusOnly ? ["z"] : ["x", "y", "z"]).every(
          (axis) => !this.hardware.axes[axis].enabled || this.manualSteps[axis].valid,
        )
      );
    },
    stepSummary() {
      if (!this.operatorControls || !this.hardware || !this.stepsValid) return undefined;
      return (this.focusOnly ? ["z"] : ["x", "y", "z"])
        .map((axis) =>
          this.hardware.axes[axis].enabled
            ? `${axis.toUpperCase()} step: ${this.manualSteps[axis].mm * 1000} µm`
            : `${axis.toUpperCase()} disabled`,
        )
        .join(" · ");
    },
    stepUnits() {
      return Object.fromEntries(
        ["x", "y", "z"].map((axis) => [axis, this.manualSteps[axis].units]),
      );
    },
    coordinatesValid() {
      return this.setPosition && Object.values(this.setPosition).every(Number.isFinite);
    },
    positionStatusUri: function () {
      return this.thingActionUrl("stage", "position");
    },
  },

  async mounted() {
    // A global signal listener to perform a move action
    if (!this.focusOnly) eventBus.on("globalMoveEvent", this.move);
    // A global signal listener to update position text boxes
    eventBus.on("globalUpdatePositionEvent", this.updatePosition);
    // A global signal listener to perform a move action in pixels
    if (!this.focusOnly) eventBus.on("globalMoveInImageCoordinatesEvent", this.onMoveImage);

    eventBus.on("stageMotionLimitsChanged", this.refreshLimits);
    eventBus.on("stageMotionDynamicsChanged", this.refreshLimits);
    // Update the current position in text boxes
    await this.updatePosition();
    this.statusTimer = setInterval(() => {
      if (this.operatorControls && !this.moveLock) this.refreshController();
    }, 2000);
  },

  beforeUnmount() {
    this.unmounted = true;
    eventBus.off("stageMotionLimitsChanged", this.refreshLimits);
    eventBus.off("stageMotionDynamicsChanged", this.refreshLimits);
    clearInterval(this.statusTimer);
    // Remove global signal listener to perform a move action
    eventBus.off("globalMoveEvent", this.move);
    eventBus.off("globalUpdatePositionEvent", this.updatePosition);
    eventBus.off("globalMoveInImageCoordinatesEvent", this.onMoveImage);
  },

  methods: {
    coordinateStep(axis) {
      const scale = this.hardware?.axes?.[axis]?.units_per_mm;
      return this.operatorControls ? (scale > 0 ? 1000 / scale : "any") : 1;
    },
    displayCoordinate(axis) {
      const value = this.setPosition?.[axis];
      if (!Number.isFinite(value)) return "";
      if (!this.operatorControls) return value;
      const scale = this.hardware?.axes?.[axis]?.units_per_mm;
      return scale > 0 ? Number(((value * 1000) / scale).toFixed(6)) : "";
    },
    setCoordinate(axis, raw) {
      const value = raw === "" ? NaN : Number(raw);
      const scale = this.hardware?.axes?.[axis]?.units_per_mm;
      this.setPosition[axis] = this.operatorControls
        ? scale > 0
          ? Math.round((value * scale) / 1000)
          : NaN
        : value;
    },
    async refreshLimits() {
      this.hardware = null;
      await this.refreshController();
    },
    async refreshController() {
      if (this.refreshing || this.unmounted) return this.controller;
      this.refreshing = true;
      try {
        if (!this.hardware) {
          this.hardware = await this.readThingProperty("stage", "hardware_settings", true);
        }
        this.controller = (await this.readThingProperty("stage", "controller_state", true)) ?? null;
        return this.controller;
      } catch {
        this.controller = null;
        return null;
      } finally {
        this.refreshing = false;
      }
    },
    async boundedStep(delta) {
      if (!this.canMove) return false;
      if (Object.entries(delta).some(([axis, value]) => value !== 0 && !this.axisEnabled[axis]))
        return false;
      this.moveLock = true;
      try {
        const response = await this.invokeAction(
          "stage",
          "move_manual",
          { ...delta, relative: true },
          false,
        );
        return await new Promise((resolve) => {
          this.pollUntilComplete(
            response.data.href,
            null,
            async (result) => {
              await this.moveComplete();
              resolve(result?.data?.status === "completed");
            },
            200,
          );
        });
      } catch (error) {
        this.controller = null;
        this.moveLock = false;
        this.modalError(error);
        return false;
      }
    },
    timeout(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    },

    onMoveImage(payload) {
      this.moveInImageCoordinatesRequest(payload.x, payload.y, payload.absolute);
    },

    async move(payload) {
      if (this.operatorControls && !this.canMove) return;
      const { x, y, z, absolute } = payload;
      // Move the stage, by updating the controls and starting a move task
      // This is equivalent to clicking the "move" button.
      if (this.moveLock) return; // Discard move requests if we're already moving
      if (this.jogging) return; // Discard move requests if a jog is in progress
      // NB moveLock is just  boolean flag - it's not as safe as a "proper" lock.
      this.moveLock = true; // This will also be set by the task submitter, but
      // setting it here avoids multiple moves being requested simultaneously.
      if (absolute) {
        this.setPosition = { x: x, y: y, z: z };
      } else {
        await this.updatePosition();
        this.setPosition = {
          x: this.setPosition.x + x,
          y: this.setPosition.y + y,
          z: this.setPosition.z + z,
        };
      }
      await this.$nextTick(); // Wait for Vue to update the position
      await this.startMoveTask();
    },
    async startMoveTask() {
      if (!this.coordinatesValid) return;
      if (this.operatorControls && !this.controller?.reference_valid) return;
      this.moveLock = true;
      await this.$refs.moveButton.startTask();
    },
    async moveComplete() {
      await this.updatePosition();
      this.moveLock = false;
    },
    async moveInImageCoordinatesRequest(x, y) {
      if (this.operatorControls) return; // Mapping is not commissioned for this stage.
      // If not movement-locked
      if (!this.moveLock) {
        // Lock move requests
        this.moveLock = true;
        const response = await this.invokeAction(
          "camera_stage_mapping",
          "move_in_image_coordinates",
          {
            x: x,
            y: y,
          },
        );
        this.pollUntilComplete(
          response.data.href,
          null, // Nothing to do while ongoing
          this.moveComplete, // Call move complete once done.
          200,
        );
      }
    },

    async updatePosition() {
      if (this.operatorControls) {
        const controller = await this.refreshController();
        if (!controller?.reference_valid) {
          this.setPosition = null;
          return;
        }
        const position = controller.position;
        if (![position?.x, position?.y, position?.z].every(Number.isFinite)) {
          this.setPosition = null;
          return;
        }
        this.setPosition = { ...position };
        return;
      }
      this.setPosition = await this.readThingProperty("stage", "position");
    },
  },
};
</script>
<style scoped>
.motor-controls {
  display: grid;
  gap: 8px;
}

.input-and-buttons-container {
  display: flex;
  flex-flow: column wrap;
  place-content: stretch flex-start;
  align-items: center;
  width: 100%;
}

.numeric-setting-line-input {
  flex-grow: 1;
  margin: 5px 0;
  width: 5em;

  /* Stop Firefox showing input spinners, other
  browsers set with block below */
  appearance: textfield;
}

/* Chrome, Safari, Edge, Opera */
.numeric-setting-line-input::-webkit-outer-spin-button,
.numeric-setting-line-input::-webkit-inner-spin-button {
  appearance: none;
}
</style>
