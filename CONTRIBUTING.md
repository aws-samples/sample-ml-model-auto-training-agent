# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug
report, new feature, correction, or additional documentation, we greatly value
feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests
to ensure we have all the necessary information to effectively respond to your
bug report or contribution.

## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest
features.

When filing an issue, please check existing open, or recently closed, issues to
make sure somebody else hasn't already reported the issue. Please try to include
as much information as you can. Details like these are incredibly useful:

- A reproducible test case or series of steps
- The version of our code being used
- Any modifications you've made relevant to the bug
- Anything unusual about your environment or deployment

## Contributing via Pull Requests

Contributions via pull requests are much appreciated. Before sending us a pull
request, please ensure that:

1. You are working against the latest source on the *main* branch.
2. You check existing open, and recently merged, pull requests to make sure
   someone else hasn't addressed the problem already.
3. You open an issue to discuss any significant work — we would hate for your
   time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Modify the source; please focus on the specific change you are contributing.
   If you also reformat all the code, it will be hard for us to focus on your
   change.
3. Ensure local tests pass. See **Running the tests** below.
4. Commit to your fork using clear commit messages.
5. Send us a pull request, answering any default questions in the pull request
   interface.
6. Pay attention to any automated CI failures reported in the pull request, and
   stay involved in the conversation.

GitHub provides additional documentation on
[forking a repository](https://help.github.com/articles/fork-a-repo/) and
[creating a pull request](https://help.github.com/articles/creating-a-pull-request/).

## Running the tests

Python (infrastructure, Lambda control plane, training runtime):

```bash
cd src
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/unit -q
```

Web console:

```bash
cd src/web
npm ci
npx vitest run
```

Infrastructure synthesis must also succeed:

```bash
cd src
CDK_DEFAULT_ACCOUNT=123456789012 CDK_DEFAULT_REGION=us-west-2 npx cdk synth --quiet
```

## Things to keep in mind for this project

- **Never commit credentials or account-specific values.** Deployment outputs
  (`src/cdk-outputs-*.json`) and local runtime config
  (`src/web/public/runtime-config.cn.js`) are gitignored because they carry
  account ids, endpoints and frontend signing keys. Placeholders belong in the
  committed files; real values stay local.
- **Skills are data, not code.** Domain knowledge belongs in
  `src/training_runtime/skills/*.md`. Adding a scenario should mean one entry in
  `cdk.json` `taskTypes` plus one Markdown skill — if you find yourself adding
  Python branches per scenario, that is a design smell worth discussing in an
  issue first.
- **The two shipped task types are examples.** `timeseries_forecast` and
  `tabular_regression` exist to demonstrate the task-type registry. Please do not
  add domain specifics of your own deployment to them; add a new task type
  instead.
- **Keep the verification path honest.** The Runner deliberately recomputes
  metrics instead of trusting the agent's self-reported numbers. Changes that
  weaken that boundary need a strong justification.
- **No proprietary binaries in the repo, and no prebuilt images.** The container
  image pulls NVIDIA CUDA wheels under proprietary terms; see NOTICE section 5.

## Finding contributions to work on

Looking at the existing issues is a great way to find something to contribute
on. As our projects, by default, use the default GitHub issue labels
(enhancement/bug/duplicate/help wanted/invalid/question/wontfix), looking at any
'help wanted' issues is a great place to start.

## Code of Conduct

This project has adopted the
[Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq)
or contact opensource-codeofconduct@amazon.com with any additional questions or
comments.

## Security issue notifications

If you discover a potential security issue in this project we ask that you
notify AWS/Amazon Security via our
[vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/).
Please do **not** create a public GitHub issue.

## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to
confirm the licensing of your contribution.
