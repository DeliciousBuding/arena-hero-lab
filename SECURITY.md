# Security policy

## Reporting a vulnerability

Please use GitHub's private security advisory feature for the repository. Do not open a
public issue containing exploit details, credentials, private datasets, or identifying
infrastructure information.

A useful report includes:

- the affected package and version or commit;
- a minimal reproduction using synthetic data;
- expected and observed behavior;
- impact and suggested remediation, if known.

## Supported code

Security fixes target the current default branch. Historical benchmark artifacts and
third-party projects linked by the leaderboard retain their own support and disclosure
policies.

## Security boundaries

- The simulator and benchmark packages process untrusted artifacts defensively.
- Publication eligibility is explicit; incomplete artifacts are rejected.
- The static web application must not embed secrets or private infrastructure metadata.
- Benchmark contestant isolation and resource limits must be implemented before untrusted
  executable contestants are accepted.
- Build and release commands do not publish unless publication is requested explicitly.
