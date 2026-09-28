# The Calibration Wizard Components

## Tasks vs Steps

The components for the wizard uses the terms "Task" and "Step" to divide up the wizard's behaviour:

* **Task**: A task is what we want to achieve. Such as "Calibrate the camera" or "Run camera stage mapping", there are multiple things to do for these complex tasks. A task can also be as simple as "welcome the user to their microscope."
* **Step**: A single page/pane of information shown to the user. The components for these can be as simple as a single `<div>` with no javascript logic.

This is done to allow complex tasks like camera stage mapping to have multiple steps:

1. An explanation and telling the user what sample to get
2. An interface for focusing
3. Actually running camera stage mapping

None of these individual steps makes sense in the wizard on their own, hence the need for the "Task" grouping. By grouping the steps we can:

- Easily include/exclude tasks based on state
- Have a consistent subtitle during a task
- Skip entire task rather than make the user click next through each pane of the task (not yet implemented)

Some simple tasks such as the welcome screen will have only one step.


## Components

### The main wizard component

The calibration wizard is a modal. The top level component in `calibrationWizard.vue` handles:

* Creating a modal
* Checking which Things that have calibration tasks are on the server
* Checking which of these Things need calibrating
* Starting modal on startup if not all Things are calibrated (with a welcome screen and just those tasks)
* Starting the modal when launched from settings, with all tasks but no welcome screen.
* Before starting the modal, a list of task components is dynamically generated.

### The main task component

The main task component `calibrationWizardTask.vue` is used to create multi-step tasks. Rather than using it directly it should be wrapped by a new component that:

* Forwards all the props
* Forwards the events back to the wizard
* Creates a list of steps.

Examples of this pattern are in `cameraCalibrationTask.vue` and `cameraStageMappingTask.vue`.

### The single step task component

For simple tasks with only one step there is no need to make both a specific task component. Instead `singleStepTask.vue` can be used, the step component can be supplied as a prop.

This is used in the main wizard to create the welcome screen and the final page.

## stepTemplateWithStream

Step components are simple enough that generally there is no need for a template. However, a number of steps have mini-stream views. To keep these consistent `stepTemplateWithStream.vue` can be used.

The template puts component content above the stream view, by default. But extra information can be added to the area below the stream by using the `<template #below-stream>` tag.

For example:

```html
<template>
  <stepTemplateWithStream>
    <p>
      <b>This is above the stream.</b>
    </p>
    <template #below-stream>
      <div>
        This text is below the stream
      </div>
    </template>
  </stepTemplateWithStream>
</template>
```

Note: Vue seems to get angry if the HTML within the `<template #below-stream>` tag is not all contained in a single `<div>`
