<template>
  <div id="stageSettings" ref="stageSettingsContainer" class="uk-width-large">
    <h3>Stage limits</h3>
    <compact-help
      class="uk-margin-small-bottom"
      label="Active controller"
      :text="`This microscope uses ${stageType}. Limits are relative to the current local zero.`"
    />
    <stage-motion-limits v-if="physicalStage" />
    <stage-motion-dynamics v-if="physicalStage" />
    <div>
      <compact-help
        v-if="physicalStage"
        class="uk-margin-top"
        label="Axis directions"
        text="Axis directions come from hardware profile direction_sign values. Changing them requires a direction check and a new local zero."
      />
      <div v-if="!physicalStage" class="uk-margin">
        <p>Your z motor is currently {{ z_inverted }} inverted.</p>
        <p>We expect that moving in +z:</p>
        <ul>
          <li>Moves your objective up, towards the sample and illumination</li>
          <li>Turns the exposed z gear anti-clockwise (when viewed from above)</li>
        </ul>
        <p>If this is not the case, click the button below to switch.</p>
        <div class="uk-margin">
          <div class="uk-margin">
            <action-button
              class="uk-width-1-2"
              thing="stage"
              action="invert_axis_direction"
              :submit-data="{ axis: 'z' }"
              submit-label="Invert z"
              @response="readAxis()"
            />
          </div>
        </div>
      </div>
      <div v-if="stageMeasureAvailable" class="uk-width-large">
        <h4>Stage Measurement Tools</h4>

        <div class="uk-margin">
          <action-button
            class="uk-width-1-2"
            thing="stage_measure"
            action="perform_recentre"
            submit-label="Recentre Stage"
            :button-primary="true"
            :can-terminate="true"
            :requires-confirmation="true"
            :confirmation-message="'Start recalibration of the stage to find the centre of the range of motion?<br><br>Calibration requires a large, dense, flat sample with a variety of features.<br>Microscope will be locked while this runs'"
            :modal-progress="true"
            :stream-with-modal="true"
          />
        </div>
        <div class="uk-margin">
          <action-button
            class="uk-width-1-2"
            thing="stage_measure"
            action="perform_rom_test"
            submit-label="Run ROM Test"
            :button-primary="true"
            :can-terminate="true"
            :requires-confirmation="true"
            :confirmation-message="'Start recalibration of the stage to find the range of motion in steps?<br><br>Calibration requires a large, dense, flat sample with a variety of features.<br>Microscope will be locked while this runs'"
            :modal-progress="true"
            :stream-with-modal="true"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import StageMotionLimits from "./stageMotionLimits.vue";
import StageMotionDynamics from "./stageMotionDynamics.vue";
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import ActionButton from "../../labThingsComponents/actionButton.vue";
import { useIntersectionObserver } from "@vueuse/core";

export default {
  name: "StageSettings",

  components: {
    ActionButton,
    CompactHelp,
    StageMotionLimits,
    StageMotionDynamics,
  },

  data: function () {
    return {
      z_inverted: "",
    };
  },

  computed: {
    physicalStage() {
      return Boolean(this.thingDescription("stage")?.properties?.hardware_settings);
    },
    stageType: function () {
      return this.thingDescription("stage").title;
    },
    stageMeasureAvailable() {
      return this.thingAvailable("stage_measure");
    },
  },

  mounted() {
    useIntersectionObserver(
      this.$refs.stageSettingsContainer,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      { threshold: 0.0 },
    );
  },

  methods: {
    visibilityChanged(isVisible) {
      if (isVisible) {
        this.readAxis();
      }
    },
    async readAxis() {
      if (this.physicalStage) return;
      let axes_inverted = await this.readThingProperty("stage", "axis_inverted");
      this.z_inverted = axes_inverted["z"] ? "" : "not ";
    },
  },
};
</script>

<style lang="less"></style>
