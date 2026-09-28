# WebApp Development

## Key info

* Vue.js web application providing a graphical interface for the Openflexure Microscope.
* Once built, will be served by the API server from the host root on port 5000
* JS client is coupled to the API, and is built and distributed with the server.


## Installing

_Note: If you are using a Windows OS, the following commands must be executed in a Powershell terminal._

* [Get Node.js v26 using the official installation scripts for your operating system.](https://nodejs.org/en/download/current) *(Optionally, you can instead install NVM as explained below)*.
* Navigate into the webapp directory with `cd webapp`
* Install dependencies with `npm install`
* Build the static web app with `npm run build`


#### ( Optional ) Using NVM "Node-Version-Manager"

If you have multiple projects that use different versions of Node.JS you can install Node Version Manager (NVM) to allow you to switch version from the terminal. To use NVM:

* [Install NVM](https://www.nvmnode.com/guide/installation.html)
* Install Node.js v26 with `nvm install 26`
* Set the Node.js to version 26 with `nvm use 26`


## Live Server

When developing it is useful to use the development server so Vue changes happen instantly without the need to rebuild. To start the development server run:

    npm run serve

The development server is accessed on a different port from the microscope API. When Vite starts, it creates a proxy that proxies all routes starting with `/api` to `http://localhost:5000/api`. This is useful when using either the simulation server or when developing directly on a microscope.

If developing the webapp on a different computer you can run:

    npm run serve:microscope

This will proxy all routes starting with `/api` to `http://microscope.local:5000/api`.

If your microscope hostname is not `microscope`, you can use local environment files to override this route. Create the file `.env.local` within the `/webapp/` directory, and in that file add the line

    VITE_MICROSCOPE_HOST=http://myhostname.local:5000

(changing `myhostname` to the correct host name.)

**Note:** If you have performance issues with lag, this can be caused by mDNS speed for your setup. To avoid mDNS, overload `VITE_MICROSCOPE_HOST` with the IP address of your microscope, rather than the host name.

## Javascript: Formatting and linting

To enforce code style we and quality we use ESLint and Prettier. Both can be run together with the same command.

- To check the for errors and warnings run `npm run lint`
- To automatically fix errors and warnings run `npm run lint:fix`

## CSS: Workflow

Our global CSS, and the CSS of some webapp components is written using the [LESS (Leaner Style Sheets) language extension](https://lesscss.org/). The LESS is then pre-processed into pure CSS.

We use [UIKit](https://getuikit.com/docs/introduction) for most of the CSS. Where possible we use UIKit CSS classes for styling. Some of the UIKit classes have some global modifications in the `webapp/src/assets/less` directory.

For styling in dark mode and light mode we make use of the UIKit `.hook_inverse()` function. Note that this function is not available in components. To create a style which needs to change in dark mode, the class (and its inverse) should be defined in `webapp/src/assets/less/variable.less`. For example:

```css
.ofm-opaque-element{
    background: #f5f5f5;
}
.hook-inverse() {
    .ofm-opaque-element{
        background: #282626;
    }
}
```

Defining colours and their inverse globally allows for considering the reasoning for the colour and defining a name based on what the colouring is achieving. This helps colouring to be consistent across the application.

For linting CSS we use Stylelint.

- To check the for errors and warnings run `npm run lint:style`
- To automatically fix errors and warnings run `npm run lint:style:fix`

##  Documentation conventions

This project uses VUE version 3 and JavaScript ECMA version 2021.

Run `npm run build:docs` to regenerate the API reference. The script runs both generators in sequence — first `vue-docgen -c docgen.config.js` for the `.vue` components, then `jsdoc2md --configure .jsdoc2md.json` for the plain JavaScript files, writing the output to `../apidocs/webapp_docs/JS.md`.

To change this behaviour refer to the package.json file.

At the following links you will find detailed information on how-to write JSDoc strings, and HTML strings to document your component.

- <a href="../webapp_docs/index.html#components" data-ignore>VUE Components</a>
- <a href="../webapp_docs/index.html#JS" data-ignore>JavaScript files</a>
- <a href="../webapp_docs/index.html#guide" data-ignore>Standard documentation guidance</a>
