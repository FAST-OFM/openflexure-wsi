<template>
  <!-- Grid managing tab content -->
  <div uk-grid class="uk-height-1-1 uk-margin-remove uk-padding-remove">
    <div class="control-component">
      <paneControl />
    </div>
    <div class="view-component uk-width-expand">
      <streamDisplay :stream-id="setStreamId" />
    </div>
  </div>
</template>

<script>
import paneControl from "./controlComponents/paneControl.vue";
import streamDisplay from "./streamContent.vue";

export default {
  name: "ControlContent",

  components: {
    paneControl,
    streamDisplay,
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
.control-component {
  width: 180px;
}
</style>
