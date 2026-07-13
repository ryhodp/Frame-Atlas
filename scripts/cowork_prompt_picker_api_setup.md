I need help setting up Google's Picker API for my app "Frame Atlas." Please do the following:

**1. Google Cloud Console (console.cloud.google.com)**
- Go to the Google Cloud project that already has OAuth credentials set up for "Frame Atlas" (it will have an existing OAuth 2.0 Client ID — look for one with a name like "Frame Atlas" or check the authorized redirect URIs for `frame-atlas-production.up.railway.app`). If more than one project could match, ask me which one before proceeding.
- Go to "APIs & Services" → "Library," search for "Google Picker API," and enable it for that project.
- Go to "APIs & Services" → "Credentials" → "Create Credentials" → "API key." This creates a new API key (separate from the existing OAuth Client ID/Secret — don't touch those).
- Click into the new API key's settings and restrict it: under "API restrictions," choose "Restrict key" and select only "Google Picker API." Save.
- Rename the key to something recognizable like "Frame Atlas Picker Key."
- Copy the key value.

**2. Railway (railway.app)**
- Go to my Railway account, project "daring-light," service "Frame-Atlas."
- Go to the "Variables" tab.
- Add a new variable named exactly `GOOGLE_PICKER_API_KEY` with the value being the API key you just copied from Google Cloud Console. Do not add quotes around the value.
- Save — this will trigger a redeploy of the app, which is expected and fine.

**3. Confirm**
- Tell me once both steps are done and the Railway variable is saved. You don't need to paste the actual key value back to me — just confirm it's set.

If you hit a permission issue or can't find the right project/service, stop and ask me rather than guessing or creating a new project.
