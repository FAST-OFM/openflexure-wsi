<template>
  <div>
    <slot></slot>
    <miniStreamDisplay class="mini-preview" :stream-id="setStreamId" />
    <slot name="below-stream"></slot>
  </div>
</template>

<script>
import miniStreamDisplay from "../../genericComponents/miniStreamDisplay.vue";

export default {
  name: "StepTemplateWithStream",

  components: {
    miniStreamDisplay,
  },

  data() {
    return {
      // This adds the parent name as value for prop streamId
      setStreamId: this.$options.name,
    };
  },
};
</script>

<style scoped>
.mini-preview {
  width: 75%;
  margin-left: auto;
  margin-right: auto;
}
</style>
