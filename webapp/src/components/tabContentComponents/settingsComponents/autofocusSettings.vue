<template>
  <div class="uk-width-large autofocus-settings">
    <h3>Autofocus</h3>
    <section class="uk-margin-bottom">
      <h4>Standard WHITE autofocus</h4>
      <compact-help
        class="uk-margin-small-bottom"
        label="Where these defaults apply"
        text="These are defaults for server-side WHITE autofocus actions. The Control range and each scan workflow keep their own explicit values."
      />
      <property-control
        thing-name="autofocus"
        property-name="default_range_um"
        label="Default sweep range (µm)"
        :read-back="true"
        :read-back-delay="0"
      />
      <div class="uk-margin-small-top">
        <property-control
          thing-name="autofocus"
          property-name="max_attempts"
          label="Maximum repeated searches"
          :read-back="true"
          :read-back-delay="0"
        />
      </div>
    </section>
    <rg-focus-settings />
    <rg-simultaneous-settings />
  </div>
</template>

<script>
import CompactHelp from "@/components/genericComponents/compactHelp.vue";
import PropertyControl from "@/components/labThingsComponents/propertyControl.vue";
import RgFocusSettings from "./cameraSettingsComponents/rgFocusSettings.vue";
import RgSimultaneousSettings from "./cameraSettingsComponents/rgSimultaneousSettings.vue";

export default {
  name: "AutofocusSettings",
  components: { CompactHelp, PropertyControl, RgFocusSettings, RgSimultaneousSettings },
};
</script>
