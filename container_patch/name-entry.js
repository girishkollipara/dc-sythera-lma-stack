// Reliable display-name entry for meeting pre-join screens.
//
// The pre-join forms are React apps that keep hydrating after their input
// element already exists in the DOM. waitForSelector resolves at "exists",
// not at "ready", so a plain element.type() silently loses whichever
// keystrokes land during hydration - Teams' anonymous ("light-meetings")
// pre-join floats a transient shroud over the form and swallows them.
//
// Observed on LMA 0.3.2 with LMA_IDENTITY="LMA ({LMA_USER})":
//   typed "LMA (user@example.com)" -> field held "Luser@example.com)"
//   typed "user@example.com"       -> field held "u"
// The amount lost depends purely on how slow the page is that run, so the
// only reliable approach is to type, read the value back, and retype until
// it matches.

const MAX_ATTEMPTS = 6;
const RETRY_DELAY_MS = 500;
const TYPE_DELAY_MS = 30;

export async function typeNameReliably(element, value) {
    if (!element) {
        return false;
    }

    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        // Focus and clear in one hop so a half-typed value from a previous
        // attempt cannot be appended to.
        await element.evaluate((el) => {
            const input = el;
            input.focus();
            input.value = '';
        });

        await element.type(value, { delay: TYPE_DELAY_MS });

        const actual = await element.evaluate((el) => el.value || '');
        if (actual === value) {
            if (attempt > 1) {
                console.log(`Display name entered correctly on attempt ${attempt}.`);
            }
            return true;
        }

        console.warn(
            `Display name mismatch (expected "${value}", got "${actual}") - ` +
            `retrying ${attempt}/${MAX_ATTEMPTS}.`
        );
        await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    }

    // Fall through rather than abort: joining with a truncated name is still
    // better than not joining and losing the recording.
    console.warn('Display name still incorrect after all attempts - joining anyway.');
    return false;
}
