<template>
  <stepTemplateWithStream>
    <div v-if="showResetCalButton">
      <p>
        <strong>
          <i> The camera is calibrated. To re-align illumination you must reset calibration. </i>
        </strong>
      </p>
      <action-button
        :button-primary="true"
        :can-terminate="false"
        thing="camera"
        action="reset_lens_shading"
        submit-label="Reset Calibration"
        @error="modalError"
        @finished="checkCalibrationState"
      />
    </div>
    <template v-else>
      <div>
        <p>Look at the microscope video stream below. Is the light <b>centred</b> in the stream?</p>
        <p><b>If not:</b></p>
        <ul class="uk-list uk-list-bullet">
          <li>Loosen the screws at the bottom of the illumination dovetail.</li>
          <li>Move the illumination horizontally to centre the light in the stream.</li>
          <li>Tighten the screws, taking care not move the illumination.</li>
        </ul>
      </div>
    </template>
    <template v-if="canAdjustExposure && !showResetCalButton" #below-stream>
      <div class="uk-flex uk-flex-center uk-margin-top">
        If the image is too dark or too bright to clearly see the light distribution across the
        stream, adjust the exposure using the buttons below.
      </div>
      <div class="uk-flex uk-flex-center uk-flex-middle button-gap">
        <button
          class="ofm-top-nav-square-button material-icon uk-button uk-button-default"
          type="button"
          @click="decreaseExposure"
        >
          <span class="material-symbols-outlined">shutter_speed_minus</span>
        </button>
        <button
          class="ofm-top-nav-square-button material-icon uk-button uk-button-default"
          type="button"
          @click="increaseExposure"
        >
          <span class="material-symbols-outlined">shutter_speed_add</span>
        </button>
      </div>
    </template>
  </stepTemplateWithStream>
</template>

<script>
import actionButton from "@/components/labThingsComponents/actionButton.vue";
import stepTemplateWithStream from "../stepTemplateWithStream.vue";
export default {
  name: "IlluminationCentreStep",

  components: {
    actionButton,
    stepTemplateWithStream,
  },

  data: function () {
    return {
      cameraCalibrated: false,
      cameraCanReset: false,
      canAdjustExposure: false,
    };
  },

  computed: {
    showResetCalButton() {
      return this.cameraCanReset && !this.cameraCalibrated;
    },
  },

  /**
   * Check calibration state and if calibration can be reset on mount.
   */
  async mounted() {
    const actions = this.thingDescription("camera").actions;
    const properties = this.thingDescription("camera").properties;
    this.canAdjustExposure = "exposure_time" in properties;
    this.cameraCanReset = "reset_lens_shading" in actions;
    await this.checkCalibrationState();
  },
  methods: {
    /**
     * Check if camera is calibrated
     */
    async checkCalibrationState() {
      this.cameraCalibrated = await this.readThingProperty("camera", "calibration_required");
    },
    async decreaseExposure() {
      let exposure = await this.readThingProperty("camera", "exposure_time");
      await this.writeThingProperty("camera", "exposure_time", Math.round(exposure * 0.666));
    },
    async increaseExposure() {
      let exposure = await this.readThingProperty("camera", "exposure_time");
      await this.writeThingProperty("camera", "exposure_time", Math.round(exposure * 1.5));
    },
  },
};
</script>
<style scoped>
.button-gap {
  gap: 35px;
}
</style>
