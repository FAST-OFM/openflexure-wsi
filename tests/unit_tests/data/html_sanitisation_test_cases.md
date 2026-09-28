# HTML Sanitisation Test Cases

This file is in markdown not Python for readability.

Each test is a level 2 heading. The input and expected values are level 3 headings. Multiline inputs and results are treated as single, multiline strings.

The HTML to test is in triple backtick code blocks.

Anything not in code blocks is a comment.

This file is run by /tests/unit_tests/test_ui.py

## Test scripts removed
The script is removed but the contents remain.
### Input
```html
<script>alert("x")</script>
<p>Hello</p>
```
### Result
```html
alert(&quot;x&quot;)
Hello
```


## Test script in bold tag
### Input
```html
<b onmouseover=alert('Wufff!')>click me!</b>
```
### Result
```html
<b>click me!</b>
```


## Test script in disallowed tag
### Input
```html
<img src="http://url.to.file.which/not.exist" onerror=alert(document.cookie);>
```
### Result
```html
```


## Test cookie grabber script is escaped
The script should become escaped and have no tags
### Input
```html
<SCRIPT type="text/javascript">
var adr = '../evil.php?cakemonster=' + escape(document.cookie);
</SCRIPT>
```
### Result
```html
var adr = &#x27;../evil.php?cakemonster=&#x27; + escape(document.cookie);
```


## Test with non-http links
Remove the javascript from the links.
### Input
```html
<a href="javascript:alert(1)">link</a>
<a href="javas&#99;ript:alert(1)">encoded</a>
<a href="mailto:spammy@spam.com">Email</a>
<a href="/a/relateive/link">Relative</a>
```
### Result
```html
<a>link</a>
<a>encoded</a>
<a>Email</a>
<a>Relative</a>
```


## Test http links become external
Remove the javascript from the links.
### Input
```html
<a href="https://openflexure.org">OpenFlexure</a>
```
### Result
```html
<a href="https://openflexure.org" target="_blank" rel="noopener noreferrer">OpenFlexure</a>
```


## Test basic formatting preserved
Check basic elements are not changed
### Input
```html
This is <b>bold</b> <i>italic</i> and on a new <br/> line.
```
### Result
```html
This is <b>bold</b> <i>italic</i> and on a new <br/> line.
```


## Test http link with path preserved
Valid HTTP links should preserve their path.
### Input
```html
<a href="https://example.com/docs/page">Docs</a>
```
### Result
```html
<a href="https://example.com/docs/page" target="_blank" rel="noopener noreferrer">Docs</a>
```


## Test http link with fragment preserved
Valid HTTP links should preserve their path and fragment.
### Input
```html
<a href="https://example.com/docs/page#section">Docs Section</a>
```
### Result
```html
<a href="https://example.com/docs/page#section" target="_blank" rel="noopener noreferrer">Docs Section</a>
```