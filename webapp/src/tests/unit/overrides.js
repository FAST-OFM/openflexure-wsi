import { markRaw } from "vue";

/**
 * @file Component-specific overrides for unit testing.
 * @description Map filenames to the specific props or mocks required
 * Not all components ask for props
 * If a component has unmet props or global mocks setup.js is set to fail the test
 **/
export const componentOverrides = {
  "galleryContent.vue": {
    global: {
      mocks: {
        readThingProperty: () => [],
      },
    },
  },
  "serverSpecifiedPropertyControl.vue": {
    props: {
      propertyData: {},
      propertyName: "",
      thingName: "",
    },
  },
  "miniStreamDisplay.vue": {
    props: { streamId: "testProp" },
  },
  "compactHelp.vue": {
    props: { label: "Help", text: "Details" },
  },
  "streamContent.vue": {
    props: { streamId: "testProp" },
  },
  "paginateLinks.vue": {
    props: { totalPages: 5, currentPage: 1 },
  },
  "simpleAccordion.vue": {
    props: { title: "Test Accordion Title" },
  },
  "tabContent.vue": {
    props: { tabID: "tab-1", currentTab: "tab-1" },
  },
  "tabIcon.vue": {
    props: { tabID: "tab-1", currentTab: "tab-1" },
  },
  "actionButton.vue": {
    props: { action: "test-action", thing: "test-thing" },
  },
  "actionLogDisplay.vue": {
    props: { taskStatus: "", log: [] },
  },
  "endActionButton.vue": {
    props: { url: "http://microscope.local/api/v3" },
  },
  "endpointButton.vue": {
    props: { url: "http://microscope.local/api/v3" },
  },
  "matrixDisplay.vue": {
    props: { matrix: [] },
  },
  "toggleSwitch.vue": {
    props: { modelValue: true },
  },
  "actionProgressBar.vue": {
    props: { taskStatus: "" },
  },
  "actionStatusModal.vue": {
    props: {
      title: "",
      log: [],
      canTerminate: true,
      taskRunning: true,
      taskStarted: true,
      taskStatus: "",
    },
  },
  "inputFromSchema.vue": {
    props: { dataSchema: {} },
  },
  "propertyControl.vue": {
    props: {
      propertyName: "",
      thingName: "test_thing",
      dataSchema: {},
    },

    mocks: {
      wotStore: {
        thingDescriptions: {
          test_thing: {
            actions: {},
            properties: {},
          },
        },
      },
    },
  },
  "serverSpecifiedActionButton.vue": {
    props: {
      actionData: {},
      elements: {},
      propertyData: {},
      action: "test-action",
      thing: "test-thing",
    },
  },
  "serverSpecifiedInterface.vue": {
    props: { elements: [] },
  },
  "slideScanControls.vue": {
    mocks: {
      readThingProperty: async (_thing, property) => {
        if (property === "workflow_name") return "fast_ofm_scan_workflow";
        if (property === "workflow_display_names") {
          return { fast_ofm_scan_workflow: "Fast OFM Scan" };
        }
        return true;
      },
    },
  },
  "calibrationWizardTask.vue": {
    props: { steps: [] },
  },
  "singleStepTask.vue": {
    props: {
      stepComponent: markRaw({ template: '<div class="dummy"></div>' }),
    },
  },
  "actionTab.vue": {
    props: {
      action: "test-action",
      thing: "test-thing",
      taskId: "",
      taskUrl: "",
    },
  },
  "galleryCard.vue": {
    props: { itemData: { gallery_info: {} } },
  },
  "openSeadragonViewer.vue": {
    props: {
      src: "",
      brightness: 0,
      contrast: 0,
      saturation: 0,
    },
  },
  "cameraCalibrationSettings.vue": {
    props: { cameraUri: "" },
  },
  "zMotorDirectionStep.vue": {
    mocks: {
      thingDescription: () => ({
        properties: {
          test_property_1: {},
          test_property_2: {},
        },
        actions: {
          calibrate_z_direction: {},
        },
      }),
    },
  },
};
