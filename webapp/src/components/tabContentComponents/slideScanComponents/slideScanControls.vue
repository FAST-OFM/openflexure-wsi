<template>
  <div ref="slideScanControls" class="slide-scan-controls">
    <template v-if="compactWizard">
      <server-specified-interface
        :elements="essentialWorkflowSettings"
        :refresh-on-saved="true"
        @request-update="workflowSettingsSaved"
      />
      <focus-scan-setup-status ref="focusSetup" :workflow-name="workflowName" />
      <simple-accordion
        title="Advanced"
        :highlighted="stitchingSettingsChanged || advancedWorkflowSettingsChanged"
      >
        <div class="scan-advanced-section">
          <label class="uk-form-label">Workflow</label>
          <select
            class="uk-select uk-form-small"
            :value="workflowName"
            @change="setWorkflow($event.target.value)"
          >
            <option v-for="(label, name) in workflowOptions" :key="name" :value="name">
              {{ label }}
            </option>
          </select>
        </div>
        <server-specified-interface
          :elements="advancedWorkflowSettings"
          :refresh-on-saved="true"
          @request-update="workflowSettingsSaved"
          @changed="advancedWorkflowSettingsChanged = $event"
        />
        <section class="scan-advanced-section">
          <h4 class="uk-margin-small-bottom">Output</h4>
          <output-controls @changed="propertyChanged" />
        </section>
      </simple-accordion>
    </template>
    <template v-else>
      <div class="uk-margin">
        <label class="uk-form-label">Workflow</label>
        <select
          class="uk-select uk-form-small"
          :value="workflowName"
          @change="setWorkflow($event.target.value)"
        >
          <option v-for="(label, name) in workflowOptions" :key="name" :value="name">
            {{ label }}
          </option>
        </select>
      </div>
      <server-specified-interface
        :elements="workflowSettings"
        :refresh-on-saved="true"
        @request-update="workflowSettingsSaved"
      />
      <focus-scan-setup-status ref="focusSetup" :workflow-name="workflowName" />
      <simple-accordion title="Output" :highlighted="stitchingSettingsChanged">
        <output-controls @changed="propertyChanged" />
      </simple-accordion>
    </template>
  </div>
</template>

<script>
import ServerSpecifiedInterface from "@/components/labThingsComponents/serverSpecifiedInterface.vue";
import SimpleAccordion from "@/components/genericComponents/simpleAccordion.vue";
import FocusScanSetupStatus from "./focusScanSetupStatus.vue";
import OutputControls from "./outputControls.vue";
import { useIntersectionObserver } from "@vueuse/core";

export default {
  name: "SlideScanControls",

  components: {
    ServerSpecifiedInterface,
    SimpleAccordion,
    FocusScanSetupStatus,
    OutputControls,
  },

  data() {
    return {
      workflowName: undefined,
      workflowSettings: [],
      workflowOptions: [],
      workflowReady: false,
      stitchingChangedProperties: {},
      advancedWorkflowSettingsChanged: false,
    };
  },

  computed: {
    compactWizard() {
      return Boolean(this.settingsGroup("fast-ofm-wizard-essential"));
    },
    essentialWorkflowSettings() {
      return this.settingsGroup("fast-ofm-wizard-essential")?.children || [];
    },
    advancedWorkflowSettings() {
      return this.settingsGroup("fast-ofm-wizard-advanced")?.children || [];
    },
    stitchingSettingsChanged() {
      return Object.values(this.stitchingChangedProperties).some(Boolean);
    },
  },

  async created() {
    this.readSettings();
    this.workflowOptions = await this.readThingProperty(
      "smart_scan",
      "workflow_display_names",
      true,
    );
  },

  mounted() {
    // sets visibilityChanged to true or false, which can then update settings if needed
    useIntersectionObserver(
      this.$refs.slideScanControls,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      {
        threshold: 0.0,
      },
    );
  },

  methods: {
    settingsGroup(cssClass) {
      return this.workflowSettings.find(
        (element) => element.element_type === "container" && element.css_class === cssClass,
      );
    },
    async workflowSettingsSaved() {
      await this.readSettings();
      await this.$refs.focusSetup?.refreshCapability();
    },
    visibilityChanged(isVisible) {
      if (isVisible) {
        this.readSettings();
      }
    },
    propertyChanged(key, changed) {
      this.stitchingChangedProperties[key] = changed;
    },
    async readSettings() {
      this.workflowName = await this.readThingProperty("smart_scan", "workflow_name");

      if (!this.workflowName) {
        console.warn("Could not read workflow_name, using default");
        this.workflowName = "fast_ofm_scan_workflow";
      }

      if (this.workflowName) {
        this.workflowReady = await this.readThingProperty(this.workflowName, "ready", true);
        this.workflowSettings =
          (await this.getThingEndpoint(this.workflowName, "settings_ui")) || [];
      }
    },
    async setWorkflow(name) {
      try {
        this.workflowName = name;

        await this.writeThingProperty("smart_scan", "workflow_name", name);

        // refresh  UI
        await this.readSettings();
      } catch (err) {
        this.modalError(err);

        // revert if server rejected
        this.workflowName = await this.readThingProperty("smart_scan", "workflow_name", true);
      }
    },
  },
};
</script>

<style lang="less" scoped>
.slide-scan-controls {
  margin-bottom: 1rem;
}

.scan-advanced-section {
  margin: 1rem 0;
}
</style>
