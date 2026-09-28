// Elite Command Center - Firebase Master Configuration
// NOTE: Firebase API keys are designed to be public identification keys.
// To ensure security, restrict this key to your domains in the Google Cloud Console.

const productionAuthDomain = typeof window !== "undefined" && window.location.hostname === "bot-bulls.web.app"
    ? "bot-bulls.web.app"
    : "bot-bulls.firebaseapp.com";

export const firebaseConfig = {
    apiKey: "AIzaSyBwuz-VKFquQ-3bnccg0PZHEEKNfwP0ap0",
    authDomain: productionAuthDomain,
    projectId: "bot-bulls",
    storageBucket: "bot-bulls.firebasestorage.app",
    messagingSenderId: "365898206658",
    appId: "1:365898206658:web:4aeedf2f743077651cda5b"
};
