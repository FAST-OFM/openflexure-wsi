<template>
  <stepTemplateWithStream>
    <p>
      <b>If the sample is in focus, click Auto-Calibrate Using Camera.</b>
    </p>
    <p>If it is not in focus, click back and re-focus.</p>
    <template #below-stream>
      <action-log-display id="log-display" :log="log" :task-status="taskStatus" />
      <div class="action-button-container">
        <action-button
          :button-primary="true"
          :can-terminate="true"
          thing="camera_stage_mapping"
          action="calibrate_xy"
          :submit-label="'Auto-Calibrate Using Camera'"
          @error="modalError"
          @update:task-status="taskStatus = $event"
          @update:log="log = $event"
          @task-started="$emit('prevent-navigation', true)"
          @finished="csmFinished"
        />
      </div>
    </template>
  </stepTemplateWithStream>
</template>

<script>
import actionButton from "@/components/labThingsComponents/actionButton.vue";
import actionLogDisplay from "@/components/labThingsComponents/actionLogDisplay.vue";
import stepTemplateWithStream from "../stepTemplateWithStream.vue";

export default {
  name: "CSMMainCalibrationStep",

  components: {
    actionButton,
    actionLogDisplay,
    stepTemplateWithStream,
  },

  emits: ["prevent-navigation", "awaiting-user"],

  data: function () {
    return {
      log: [],
      taskStatus: "",
    };
  },

  mounted() {
    this.checkCalibrationState();
  },

  methods: {
    /**
     * Check if camera stage mapping is calibrated and emit the result to awaiting-user.
     *
     * "awaiting-user" is used to signal that the user should perform an action before
     * continuing. In practice it changes the next button to "skip".
     */
    async checkCalibrationState() {
      const needsCal = await this.readThingProperty("camera_stage_mapping", "calibration_required");
      this.$emit("awaiting-user", needsCal);
    },
    /**
     * Runs whenever CSM finishes. It re-enables navigation and checks calibration state.
     *
     * This happens if the action is cancelled, errors, or completes successfully as it
     * is triggered by `finished`.
     */
    async csmFinished() {
      this.$emit("prevent-navigation", false);
      await this.checkCalibrationState();
    },
  },
};
</script>

<style scoped>
.action-button-container {
  padding: 4px;
  display: flex;
  justify-content: center;
}
</style>
