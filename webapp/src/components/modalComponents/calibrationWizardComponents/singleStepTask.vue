<!-- A component to make a task with just one step

The calibration wizard makes a distinction between tasks (what you are trying to
achieve, such as calibrating the camera) and steps (individual pages/panes shown to the
user). The down side of this is there is some complexity in handling tasks that is
unhelpful when trying to add a single pane.

This component takes one step Component (which can be as simple as a <div> with some
text) as the :stepComponent prop, and optionally any props to be sent to the component
can be passed as an object to :stepProps. All other prop are automatically
supplied by the wizard.
-->
<template>
  <calibrationWizardTask
    :title="title"
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
import { markRaw } from "vue";

export default {
  name: "SingleStepTask",
  components: {
    calibrationWizardTask,
  },

  props: {
    // This must be sent
    stepComponent: {
      type: Object,
      required: true,
    },
    // This is optional.
    stepProps: {
      type: Object,
      default: () => ({}),
    },
    // Standard calibrationWizardTask props below:
    title: {
      type: String,
      default: null,
    },
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
      steps: [{ component: markRaw(this.stepComponent), props: this.stepProps }],
    };
  },
};
</script>
