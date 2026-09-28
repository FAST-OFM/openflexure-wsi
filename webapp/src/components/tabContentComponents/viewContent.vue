<template>
  <!-- Grid managing tab content -->
  <div uk-grid class="uk-height-1-1 uk-margin-remove uk-padding-remove">
    <div class="view-component uk-width-expand">
      <streamDisplay :stream-id="setStreamId" />
    </div>
  </div>
</template>

<script>
import streamDisplay from "./streamContent.vue";

export default {
  name: "ViewContent",

  components: {
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
