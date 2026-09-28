<template>
  <stepTemplateWithStream>
    <ul>
      <li>
        Check in the video stream below that you are viewing though an empty area (ideally looking
        through the coverslip if your slide has one).
      </li>
      <li>
        No feature should be visible. If dust or debris from the slide is visible, use buttons below
        to defocus slightly.
      </li>
    </ul>
    <template #below-stream>
      <positionControl focus-only />
    </template>
  </stepTemplateWithStream>
</template>

<script>
import stepTemplateWithStream from "../stepTemplateWithStream.vue";
import positionControl from "@/components/tabContentComponents/controlComponents/positionControl.vue";
export default {
  name: "CameraFocusStep",

  components: {
    stepTemplateWithStream,
    positionControl,
  },
};
</script>
<style scoped></style>
