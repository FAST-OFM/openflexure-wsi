<template>
  <div id="CSMCalibrationSettings" ref="CSMCalibrationSettingsContainer" class="uk-width-large">
    <!--Show auto calibrate if default plugin is enabled-->
    <div class="uk-margin-small">
      <action-button
        :button-primary="true"
        :can-terminate="true"
        :requires-confirmation="true"
        :confirmation-message="'Start recalibration of the stage to the camera? This may take a while, and the microscope will be locked during this time.'"
        thing="camera_stage_mapping"
        action="calibrate_xy"
        :submit-label="'Auto-Calibrate Using Camera'"
        :modal-progress="true"
        :stream-with-modal="true"
        @response="onRecalibrateResponse"
        @error="modalError"
      />
    </div>
    <button
      v-show="showExtraSettings"
      type="button"
      class="uk-button uk-button-default uk-width-1-1"
      :disabled="!csmMatrix"
      :class="{ 'uk-disabled': !csmMatrix }"
      @click="getCalibrationData()"
    >
      Download Calibration Data
    </button>
    <div v-if="csmMatrix" style="margin: 10px">
      <details>
        <summary>Calibration Details</summary>
        <ul>
          <li>
            <strong>Current CSM Matrix:</strong>
            <br />
            <matrixDisplay :matrix="csmMatrix" />
          </li>
          <li>
            CSM calculated for images with a resolution of:
            <br />
            <matrixDisplay :matrix="csmResolution" :bracket-height="1.5" />
          </li>

          <li>
            {{ physicalStage ? "Image X/Y scale (µm/px)" : "Image X/Y scale (stage units/px)" }}:
            {{ csmRatio }}
          </li>
          <li>
            Full field of view ({{ physicalStage ? "µm" : "stage units" }}):
            <br />
            <matrixDisplay :matrix="csmFOV" :bracket-height="1.5" />
          </li>
        </ul>
      </details>
    </div>
    <p v-else><strong>No Calibration Data Available</strong></p>
  </div>
</template>

<script>
import ActionButton from "@/components/labThingsComponents/actionButton.vue";
import matrixDisplay from "@/components/ui/matrixDisplay.vue";
import { useIntersectionObserver } from "@vueuse/core";

// Export main app
export default {
  name: "CSMCalibrationSettings",

  components: {
    ActionButton,
    matrixDisplay,
  },

  props: {
    showExtraSettings: {
      type: Boolean,
      required: false,
      default: true,
    },
  },

  emits: ["recalibrateResponse"],

  data() {
    return {
      csmMatrix: undefined,
      csmResolution: undefined,
      csmRatio: undefined,
      csmFOV: undefined,
    };
  },

  computed: {
    physicalStage() {
      return Boolean(this.thingDescription("stage")?.properties?.hardware_settings);
    },
  },

  mounted() {
    useIntersectionObserver(
      this.$refs.CSMCalibrationSettingsContainer,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      { threshold: 0.0 },
    );
  },

  methods: {
    visibilityChanged(isVisible) {
      if (isVisible) {
        this.updateDisplayedCSM();
      }
    },
    getCalibrationData: async function () {
      try {
        let data = await this.readThingProperty("camera_stage_mapping", "last_calibration");
        if (data == {}) {
          throw "No calibration data available.";
        }
        const dataStr = JSON.stringify(data);
        const url = window.URL.createObjectURL(new Blob([dataStr]));
        const link = document.createElement("a");
        link.href = url;
        link.setAttribute("download", "csm_calibration.json");
        document.body.appendChild(link);
        link.click();
      } catch (error) {
        this.modalError(error); // Let mixin handle error
      }
    },
    updateDisplayedCSM: async function () {
      let csmMatrix = await this.readThingProperty(
        "camera_stage_mapping",
        "image_to_stage_displacement_matrix",
      );
      if (csmMatrix) {
        this.csmResolution = await this.readThingProperty(
          "camera_stage_mapping",
          "image_resolution",
        );
        let scale = [1, 1];
        if (this.physicalStage) {
          const hardware = await this.readThingProperty("stage", "hardware_settings", true);
          if (!hardware?.axes?.x?.units_per_mm || !hardware?.axes?.y?.units_per_mm) {
            this.csmMatrix = undefined;
            return;
          }
          scale = [1000 / hardware.axes.x.units_per_mm, 1000 / hardware.axes.y.units_per_mm];
        }
        // Matrix rows are image Y/X; columns are stage X/Y. Keep both components
        // for rotated cameras and convert each stage axis using its own scale.
        const [imageY, imageX] = csmMatrix.map((row) =>
          Math.hypot(row[0] * scale[0], row[1] * scale[1]),
        );
        this.csmMatrix = csmMatrix.map((row) => row.map((value) => Number(value.toFixed(3))));
        this.csmRatio = [imageX, imageY].map((value) => value.toFixed(3)).join(" / ");
        this.csmFOV = [
          Number((this.csmResolution[1] * imageX).toFixed(1)),
          Number((this.csmResolution[0] * imageY).toFixed(1)),
        ];
      }
    },
    onRecalibrateResponse: function (response) {
      this.modalNotify("Finished stage-to-camera calibration.");
      this.updateDisplayedCSM();
      this.$emit("recalibrateResponse", response);
    },
  },
};
</script>

<style lang="less">
.center-spinner {
  margin-left: auto;
  margin-right: auto;
}
</style>
