<template>
  <div>
    <ul class="uk-list uk-list-bullet">
      <li>Look at the exposed z gear from above.</li>
      <li>Press and hold the <b>Turn z motor</b> button to see the gear turn.</li>
    </ul>
    <div class="uk-margin uk-flex uk-flex-center">
      <button
        class="uk-button uk-button-primary jog-z-btn"
        type="button"
        @pointerdown="jogZ"
        @pointerup="jogZStop"
        @pointercancel="jogZStop"
      >
        <span>Turn z motor</span>
      </button>
    </div>
    <p>Which way does the z gear turn?</p>
    <!-- Direction selection -->
    <div class="uk-margin uk-flex">
      <!-- Anti-clockwise column -->
      <div class="uk-width-1-2 dir-option">
        <img
          src="/calibration_images/direction_AC.png"
          alt="Anti-clockwise"
          class="dir-image clickable"
          @click="reportDirection('anti-clockwise')"
        />

        <button
          class="uk-button uk-button-default uk-width-1-1 uk-button-primary"
          type="button"
          @click="reportDirection('anti-clockwise')"
        >
          Anti-clockwise
        </button>
      </div>

      <!-- Clockwise column -->
      <div class="uk-width-1-2 dir-option">
        <img
          src="/calibration_images/direction_CW.png"
          alt="Clockwise"
          class="dir-image clickable"
          @click="reportDirection('clockwise')"
        />

        <button
          class="uk-button uk-button-default uk-width-1-1 uk-button-primary"
          type="button"
          @click="reportDirection('clockwise')"
        >
          Clockwise
        </button>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: "ZMotorDirectionStep",

  emits: ["advance", "awaiting-user"],

  data: function () {
    return {
      jogIntervalId: null,
      jogDistance: 100,
      jogTime: 300,
    };
  },

  async mounted() {
    this.checkCalibrationState();
  },

  methods: {
    jogZ(pointerEvent) {
      if (pointerEvent.button !== 0) return;

      if (this.jogIntervalId) {
        clearInterval(this.jogIntervalId);
      }

      pointerEvent.target.setPointerCapture(pointerEvent.pointerId);

      let invokeJog = () => this.invokeAction("stage", "jog", { z: this.jogDistance });
      invokeJog();
      this.jogIntervalId = setInterval(invokeJog, this.jogTime);
    },
    jogZStop() {
      if (this.jogIntervalId) {
        clearInterval(this.jogIntervalId);
      }
      this.invokeAction("stage", "jog", { stop: true });
    },
    /**
     * Report the direction to the microscope then advance to next pane in wizard.
     */
    async reportDirection(direction) {
      await this.invokeAction("stage", "calibrate_z_direction", { positive_motion: direction });
      this.$emit("advance");
    },
    async checkCalibrationState() {
      let needsCal = await this.readThingProperty("stage", "calibration_required");
      if (needsCal) {
        // If it needs calibration check the relevant calibration action exists.
        const actions = this.thingDescription("stage").actions;
        if (!("calibrate_z_direction" in actions)) {
          console.error(
            "Stage is requesting calibration but has no 'calibrate_z_direction' action",
          );
          needsCal = false;
        }
      }
      this.$emit("awaiting-user", needsCal);
    },
  },
};
</script>

<style lang="less">
.jog-z-btn {
  display: flex;
  align-items: center;
  padding: 0 30px;
  gap: 4px;
}

.dir-option {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  margin: 5px;
}

.dir-image {
  width: 70%;
  height: auto;
}

.clickable {
  cursor: pointer;
}

.dir-option button {
  text-align: center;
}
</style>
