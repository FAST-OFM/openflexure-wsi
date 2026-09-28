<template>
  <calibrationWizardTask
    title="Camera-Stage Mapping"
    :first="first"
    :final="final"
    :start-on-last="startOnLast"
    :steps="steps"
    @next="$emit('next')"
    @back="$emit('back')"
  />
</template>

<script>
import calibrationWizardTask from "./calibrationWizardTask.vue";
import csmExplanation from "./csmSteps/csmExplanation.vue";
import focusStep from "./csmSteps/focusStep.vue";
import runCsmStep from "./csmSteps/runCsmStep.vue";
import { markRaw } from "vue";

export default {
  name: "CameraStageMappingTask",
  components: {
    calibrationWizardTask,
  },
  props: {
    // Standard calibrationWizardTask props below:
    first: Boolean,
    final: Boolean,
    startOnLast: {
      type: Boolean,
      default: false,
    },
  },
  emits: ["next", "back"],

  data: function () {
    return {
      steps: [
        { component: markRaw(csmExplanation) },
        { component: markRaw(focusStep) },
        { component: markRaw(runCsmStep) },
      ],
    };
  },
};
</script>
