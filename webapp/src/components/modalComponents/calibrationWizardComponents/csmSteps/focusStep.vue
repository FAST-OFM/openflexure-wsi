<template>
  <stepTemplateWithStream>
    <p>Use the buttons below to bring the sample into focus.</p>
    <p>Uses the same Z step as Control. Enable motors and set a local zero in Control first.</p>
    <template #below-stream>
      <positionControl focus-only />
    </template>
  </stepTemplateWithStream>
</template>

<script>
import stepTemplateWithStream from "../stepTemplateWithStream.vue";
import positionControl from "@/components/tabContentComponents/controlComponents/positionControl.vue";
export default {
  name: "CSMFocusStep",

  components: {
    stepTemplateWithStream,
    positionControl,
  },
};
</script>
<style scoped></style>
