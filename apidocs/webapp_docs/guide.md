# Component & Script Documentation Guide

This guide covers the templates, placement rules, and field-by-field instructions for documenting Vue UI components and plain JavaScript files (helpers, Pinia stores, configuration). Following it keeps the codebase maintainable and lets the automated tools (`vue-docgen` and `jsdoc2md`) generate accurate API references.

## Opt-in vs opt-out

The two toolchains have **opposite defaults**:

- **Vue components (`vue-docgen`) are opt-in for methods.** A method is documented only if tagged `@public`. Props, events, slots, and the component description are picked up automatically.
- **Plain JS files (`jsdoc2md`) are opt-out.** Every exported symbol is documented unless tagged `@private` or `@inner`.

On the Vue side, add tags to *reveal*; on the JS side, add tags to *hide*.

---

## Vue Components (Options API)

`vue-docgen` reads the Abstract Syntax Tree (AST) of the code, so **where** a comment is placed matters as much as what it says.

By default, the generated Markdown contains only: the component description, props, events, slots, and public methods.

### Template: Vue Component

Adapt the names and payloads to the target component.

```html
<template>
  <div class="custom-card">
    <!-- @slot Header region of the card. Place title/actions here. -->
    <slot name="header"></slot>
  </div>
</template>

<script>
/**
 * The main container component for displaying interactive data cards.
 * This description will appear at the very top of the generated Markdown.
 *
 * @displayName Interactive Data Card
 */

import { eventBus } from "@/eventBus";

export default {
  name: 'InteractiveCard',
  props: {
    /**
     * The unique identifier for the card's data node.
     * Type, required, and default are inferred automatically.
     */
    nodeId: {
      type: String,
      required: true
    }
  },

  methods: {
    /**
     * Triggers a manual refresh of the card data.
     * Requires the `@public` tag, or vue-docgen ignores this method.
     *
     * @public
     */
    refreshData() {
      // Logic here...

      /**
       * Fired when the data refresh successfully completes.
       * The comment must sit directly above the $emit call.
       *
       * @event data-refreshed
       * @property {number} timestamp - The exact time of completion
       */
      this.eventBus.emit('data-refreshed', { timestamp: Date.now() });
    }
  }
}
</script>
```

### Filling each section

| Section | Where the comment goes | Key tags |
| --- | --- | --- |
| Description | Block comment above `export default` | `@displayName Friendly Name` to override the auto title |
| Props | Directly above each prop key | *(none — describe purpose only)*; `@values a, b, c` for enums |
| Events | Directly above the `$emit` call | `@event name`, `@property {type} name - desc` per payload field |
| Slots | Above the `<slot>` in the template | `<!-- @slot Description -->` |
| Methods | Above the method definition | `@public` (required, or omitted) |

- **Props:** do not write `@type` or `@param` — type, `required`, and `default` are inferred from the prop definition. Describe the purpose only.
- **Events:** document the payload with `@property` lines; place the event comment directly above `$emit`, not inside the method's own block.

---

## Plain JavaScript Files (Helpers, Stores, Config)

Strict JSDoc on pure JavaScript files (`src/helpers`, `src/stores`, etc.) keeps IDE IntelliSense accurate and lets `jsdoc2md` document the logic layer.

### Start every file with `@module`

Without `@module`, a file's symbols fall into the **global** namespace: ungrouped, with no parent section. With it, the file becomes a named section and its exports nest underneath.

Put `@module` on its own line, in its own block, at the top of the file:

```javascript
/**
 * Settings and state related to the microscope connection.
 * @module settings
 */
```

The name after `@module` becomes the section heading (`## settings`). Use the logical module name, not the filename.

### Template: Pinia Store (setup store)

A Pinia setup store is an exported constant assigned from `defineStore`. Document it as a `@constant`. Because the store function returns the reactive state and actions, note that with `@returns {Object}`. Place the description block directly above the `export const`.

```javascript
/**
 * Settings and state related to the microscope connection.
 * @module settings
 */

import { defineStore } from "pinia";
import { ref } from "vue";

/**
 * A Pinia store for managing application settings and state related to the
 * microscope connection. Includes connection status, error messages, active
 * streams, and user preferences such as theme and navigation settings, plus
 * actions to reset state, set connection status, and manage active streams.
 *
 * @constant
 * @returns {Object} The store's reactive state and actions.
 */
export const useSettingsStore = defineStore(
  "settings",
  () => {
    // State
    const baseUri = ref(getOriginFromLocation());
    const ready = ref(false);

    // Actions
    function resetState() {
      ready.value = false;
    }

    return { baseUri, ready, resetState };
  },
);
```

### Template: JS Functions

Place the comment block directly above the function declaration. Document all parameters and the return type.

```javascript
/**
 * Formats a raw date string into a localized human-readable format.
 *
 * @param {string} dateString - The raw ISO date string from the API
 * @param {boolean} [includeTime=false] - Whether to include hours and minutes
 * @returns {string} The localized date string (e.g., "Jan 1, 2026")
 */
export function formatDate(dateString, includeTime = false) {
  // function logic...
}
```

- **`@param {type} name - desc`** — one line per argument.
- **Optional params:** wrap the name in brackets — `[includeTime]`.
- **Defaults:** add `=value` inside the brackets — `[includeTime=false]`.
- **Types:** union `{string|number}`, array `{Array<string>}` or `{string[]}`, object `{Object}`.
- **`@returns {type} desc`** — omit for functions that return nothing.

### Template: JS Objects & Constants

Declare the type as `Object` and map out the internal properties so the object's shape is clear without console-logging it. Use dotted names for nested fields.

```javascript
/**
 * Default step sizes for navigation via the control pane and key presses.
 *
 * @type {Object}
 * @property {number} x - Step size along the X axis
 * @property {number} y - Step size along the Y axis
 * @property {number} z - Step size along the Z axis
 */
export const navigationStepSize = {
  x: 200,
  y: 200,
  z: 50,
};
```

- Each **`@property`** becomes a row in a generated **Properties** table.
- **Nested objects:** use dotted names (e.g. `retry.max`) to render each field as its own row.

### `@static` is inferred

An `export const` or `export function` inside a `@module` file is automatically a **static** member of that module — no `@static` needed. Add it explicitly only when manually attaching a **non-exported** symbol to a module (e.g. via `@memberof`).

### Hiding a symbol

Any non-exported helper is documented as an **inner** member by default. To keep an internal helper out of the reference, add **`@private`**. The build runs with `"private": false`, so `@private` symbols are dropped.

```javascript
/**
 * Get the origin from the current window location.
 *
 * @private
 * @returns {string} The origin URL (e.g., "http://microscope.local:5000/api/v3").
 */
function getOriginFromLocation() {
  let url = new URL(window.location.href);
  return `${url.origin}/api/v3`;
}
```

---

## Quick reference

| Goal | Vue component | Plain JS file |
| --- | --- | --- |
| Set the section title | `@displayName Name` | `@module name` |
| Document a value/prop | Comment above the prop (type inferred) | `@type` + `@property` rows |
| Document a function | `@public` + describe | `@param` / `@returns` |
| Document a store | *(n/a)* | `@constant` + `@returns {Object}` above `export const` |
| Document an event | `@event` + `@property` above `$emit` | *(n/a)* |
| Document a slot | `<!-- @slot ... -->` in template | *(n/a)* |
| Show a method | Add `@public` (opt-in) | Documented by default |
| Hide something | Omit `@public` | Add `@private` (opt-out) |

---

## How the tooling is configured

This section states the versions, working files, and the options relevant to this project. Consult the official docs (linked below) for every other option, as flags and defaults change between versions.

### jsdoc2md (JavaScript files)

The JS API reference is generated with **jsdoc-to-markdown 9.1.3** (which bundles **jsdoc 4.0.5** as the parser and **dmd 6.2.3** as the Markdown template engine).

**Working files:**

- `.jsdoc2md.json` — the main jsdoc2md config (input files, template, partials, formatting).
- `jsdoc.conf.json` — the jsdoc parser config, referenced from `.jsdoc2md.json` via `configure`.
- `apidocs/webapp_docs/jsdoc-template.hbs` — the root Handlebars template.
- `apidocs/webapp_docs/partials/*.hbs` — partial overrides that customise rendering.

**Run it:**

```bash
jsdoc2md -c .jsdoc2md.json > apidocs/webapp_docs/JS.md
```

**Relevant options:**

- `files` — an **explicit list** of the `.js` files to document. Negation globs (`!**/*.spec.js`) are **not** honoured when read from the config file; list the files to include rather than excluding the ones to skip.
- `configure` — points at `jsdoc.conf.json`. That file sets `"private": false`, which drops any symbol tagged `@private` from the output.
- `template` / `partial` — point at the root template and partial overrides.
- `heading-depth: 2` — modules render at `##`, their members nest at `###`/`####`.
- `member-index-format` / `module-index-format` / `global-index-format: "none"` — suppress the auto-generated index lists.
- `param-list-format` / `property-list-format: "table"` — render `@param` and `@property` as tables.

To document a new file, **add its path to the `files` array** in `.jsdoc2md.json`. To hide a symbol inside a documented file, tag it `@private` (see "Hiding a symbol" above).

Official docs: <https://github.com/jsdoc2md/jsdoc-to-markdown/wiki> · jsdoc tags: <https://jsdoc.app>

### vue-docgen (Vue components)

The component reference is generated with **vue-docgen-cli** (current release **4.79.0**; it uses **vue-docgen-api** to parse components into documentation objects).

**Working files:**

- `docgen.config.js` — the vue-docgen configuration (component location, output, templates).

**Run it:**

```bash
vue-docgen -c docgen.config.js
```

**Relevant options:**

- `componentsRoot` — the folder where the CLI starts searching for components.
- `components` — the glob defining which files are documented (relative to `componentsRoot`), e.g. `**/[A-Z]*.vue`.
- `outDir` — the folder where generated docs are written.
- `outFile` — when set, all component docs are concatenated into a single file (relative to `outDir`).
- `getDocFileName` — controls the per-component output name; returning `false` disables separate files, enforcing the single `outFile`.
- `templates` — custom render functions that override vue-docgen's default output (used to skip components that contain no documentation).
- `apiOptions` — passed through to vue-docgen-api; set `jsx: true` only if components use JSX, and mirror the webpack/vite aliases so imports resolve.

All CLI arguments except `--config` can be moved into `docgen.config.js`. To document new components, adjust the `components` glob or `componentsRoot` rather than listing files individually.

Official docs: <https://vue-styleguidist.github.io/docs/docgen-cli.html> · custom tags: <https://vue-styleguidist.github.io/docs/Docgen.html>

### Regenerating all documentation

Run both generators in sequence with:

```bash
npm run build:docs
```

This runs `vue-docgen -c docgen.config.js` first, then `jsdoc2md --configure .jsdoc2md.json`, writing the JavaScript output to `../apidocs/webapp_docs/JS.md`. Because the two commands are joined with `&&`, `jsdoc2md` runs only if `vue-docgen` succeeds.