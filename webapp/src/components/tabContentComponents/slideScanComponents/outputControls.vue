<template>
  <div class="uk-margin">
    <property-control
      thing-name="smart_scan"
      property-name="stitch_automatically"
      label="Stitch automatically"
      @changed="$emit('changed', 'stitch_automatically', $event)"
    />
  </div>
  <div class="uk-margin">
    <property-control
      v-if="thingAvailable('smart_scan')"
      thing-name="smart_scan"
      property-name="stitch_tiff"
      label="Keep pyramidal OME-BigTIFF"
      @changed="$emit('changed', 'stitch_tiff', $event)"
    />
  </div>
</template>

<script>
import PropertyControl from "@/components/labThingsComponents/propertyControl.vue";

export default {
  name: "OutputControls",
  components: { PropertyControl },
  emits: ["changed"],
};
</script>
