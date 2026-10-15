// Where the site finds the API. Empty means the same origin, which is how the API serves it.
// To host the site somewhere else (GitHub Pages, Cloudflare Pages), set api to the API's URL,
// for example "https://www.govtechindex.cloud".
//
// contact: the address shown for sponsorship, corrections and submissions.
// sponsor: null shows "Interested in sponsoring the index? Get in touch". To credit a sponsor,
// replace null with an object like this (logo is optional; put the file next to index.html):
//   sponsor: {
//     name: "Example Ltd",
//     url: "https://example.com",
//     logo: "sponsor-logo.svg",
//     since: "October 2026",
//     blurb: "One sentence on what the sponsor does.",
//   },
window.OGI_CONFIG = {
  api: "",
  contact: "contact@andreasthinks.me",
  sponsor: null,
};
