// Nightly firmware validation pipeline.
//
//   Fetch ─► ┬─ BMC functional  (QEMU boot ─► smoke gate ─► Redfish/IPMI suites)
//            ├─ Firmware security (SBOM ─► Grype ─► CVE delta gate vs. last good build)
//            └─ GPU diagnostics  (optional, runs on a 'gpu' labelled agent)
//          ─► Dashboard (aggregated JUnit + trend)
//
// Every producer writes JUnit into reports/, so Jenkins' test view, the trend
// graph and the HTML dashboard all read from one place.

pipeline {
  agent { label 'qemu' }

  options {
    timestamps()
    timeout(time: 3, unit: 'HOURS')
    disableConcurrentBuilds()          // one QEMU instance owns the forwarded ports
    buildDiscarder(logRotator(numToKeepStr: '60', artifactNumToKeepStr: '20'))
  }

  triggers { cron('H 2 * * *') }

  parameters {
    choice(name: 'MACHINE', choices: ['romulus'], description: 'OpenBMC machine to validate')
    string(name: 'CANDIDATE_BUILD', defaultValue: 'lastSuccessfulBuild',
           description: 'Upstream OpenBMC Jenkins build number (or alias) under test')
    string(name: 'BASELINE_BUILD', defaultValue: '',
           description: 'Upstream build to diff against. Empty = CVE report of the last passing run of this job')
    booleanParam(name: 'RUN_DESTRUCTIVE', defaultValue: true,
                 description: 'Run tests that mutate BMC state (safe: QEMU boots a fresh flash copy)')
    booleanParam(name: 'RUN_GPU', defaultValue: false, description: 'Run GPU diagnostics on a gpu agent')
  }

  environment {
    PROFILE  = "qemu-${params.MACHINE}"
    VENV     = "${WORKSPACE}/.venv"
    PATH     = "${WORKSPACE}/.venv/bin:${env.PATH}"
    PIP_DISABLE_PIP_VERSION_CHECK = '1'
  }

  stages {
    stage('Setup & unit tests') {
      steps {
        sh '''
          python3 -m venv "$VENV"
          pip install -q -e '.[dev]'
          ruff check src tests
          mkdir -p reports
          pytest tests/unit -q --junitxml=reports/unit.xml -o junit_suite_name=unit
        '''
      }
    }

    stage('Fetch firmware') {
      steps {
        script {
          env.IMAGE_DIR = sh(returnStdout: true, script: """
            bmcval fetch --machine ${params.MACHINE} --build ${params.CANDIDATE_BUILD}
          """).trim().readLines().last()
          env.FIRMWARE_LABEL = "${params.MACHINE}#" + sh(returnStdout: true,
            script: "python3 -c 'import json;print(json.load(open(\"${env.IMAGE_DIR}/metadata.json\"))[\"build\"])'").trim()
          currentBuild.description = env.FIRMWARE_LABEL
        }
      }
    }

    stage('Validate') {
      parallel {
        stage('BMC functional') {
          steps {
            sh '''
              bmcval boot --profile "$PROFILE" \
                --image "$(ls "$IMAGE_DIR"/*.static.mtd)" --workdir build/qemu
            '''
            // Smoke first: if the service root is broken, 60 red tests add no information.
            sh 'pytest tests/functional -m smoke --profile "$PROFILE" --junitxml=reports/bmc-smoke.xml -o junit_suite_name=bmc.smoke'
            script {
              def destructive = params.RUN_DESTRUCTIVE ? '--run-destructive' : ''
              def rc = sh(returnStatus: true, script: """
                UPDATE_IMAGE=\$(ls "\$IMAGE_DIR"/*.static.mtd.tar 2>/dev/null || true) \
                pytest tests/functional -m 'not smoke' --profile "\$PROFILE" ${destructive} \
                  --junitxml=reports/bmc-functional.xml -o junit_suite_name=bmc.functional
              """)
              if (rc != 0) { unstable('functional test failures') }
            }
          }
          post {
            always {
              sh 'bmcval stop --workdir build/qemu || true'
              archiveArtifacts artifacts: 'build/qemu/console.log, build/qemu/boot-metrics.json',
                               allowEmptyArchive: true
            }
          }
        }

        stage('Firmware security') {
          steps {
            sh 'scripts/firmware-scan.sh "$IMAGE_DIR" reports/security/candidate'
            script {
              if (params.BASELINE_BUILD?.trim()) {
                sh """
                  BASE=\$(bmcval fetch --machine ${params.MACHINE} --build ${params.BASELINE_BUILD} --kinds mtd,spdx,manifest | tail -n1)
                  scripts/firmware-scan.sh "\$BASE" reports/security/baseline
                """
              } else {
                // Regression gate: compare against what the last *passing* nightly shipped.
                copyArtifacts(projectName: env.JOB_NAME, selector: lastSuccessful(),
                              filter: 'reports/security/candidate/grype.json, reports/security/candidate/image.manifest',
                              target: 'build/baseline', optional: true, flatten: true)
                sh '''
                  mkdir -p reports/security/baseline
                  if [ -f build/baseline/grype.json ]; then
                    cp build/baseline/grype.json reports/security/baseline/grype.json
                    cp build/baseline/image.manifest reports/security/baseline/ 2>/dev/null || true
                  else
                    echo "first run: no baseline yet, diffing candidate against itself"
                    cp reports/security/candidate/grype.json reports/security/baseline/grype.json
                  fi
                '''
              }
              def rc = sh(returnStatus: true, script: '''
                bmcval cve-diff \
                  --baseline reports/security/baseline/grype.json \
                  --candidate reports/security/candidate/grype.json \
                  --baseline-label baseline --candidate-label "$FIRMWARE_LABEL" \
                  --policy security/policy.yaml \
                  $([ -f reports/security/baseline/image.manifest ] && echo --baseline-manifest reports/security/baseline/image.manifest) \
                  $([ -f reports/security/candidate/image.manifest ] && echo --candidate-manifest reports/security/candidate/image.manifest) \
                  --markdown reports/security/cve-delta.md \
                  --junit reports/security-cve-gate.xml
              ''')
              if (rc == 1) { error('CVE gate: new vulnerabilities at or above policy threshold') }
              if (rc != 0) { error("cve-diff infrastructure failure (exit ${rc})") }
            }
          }
          post {
            always {
              archiveArtifacts artifacts: 'reports/security/candidate/*.json, reports/security/candidate/image.manifest, reports/security/cve-delta.md',
                               allowEmptyArchive: true
            }
          }
        }

        stage('GPU diagnostics') {
          when { beforeAgent true; expression { params.RUN_GPU } }
          agent { label 'gpu' }
          steps {
            sh '''
              python3 -m venv .venv && .venv/bin/pip install -q -e .
              cmake -S gpu -B build/gpu -DCMAKE_BUILD_TYPE=Release && cmake --build build/gpu -j
              mkdir -p reports/gpu
              build/gpu/gpu-health --json reports/gpu/health.json || true
              .venv/bin/bmcval gpu-health reports/gpu/health.json --junit reports/gpu-health.xml || true
              if command -v dcgmi >/dev/null; then
                dcgmi diag -r 2 -j > reports/gpu/dcgm.json || true
                .venv/bin/bmcval dcgm reports/gpu/dcgm.json --junit reports/gpu-dcgm.xml || true
              fi
              journalctl -k --since "-24h" --no-pager > reports/gpu/kern.log 2>/dev/null || dmesg > reports/gpu/kern.log
              .venv/bin/bmcval xid reports/gpu/kern.log --junit reports/gpu-xid.xml || true
            '''
            stash name: 'gpu-reports', includes: 'reports/gpu*.xml, reports/gpu/**', allowEmpty: true
          }
        }
      }
    }
  }

  post {
    always {
      script {
        if (params.RUN_GPU) { catchError(buildResult: null) { unstash 'gpu-reports' } }
      }
      junit testResults: 'reports/**/*.xml', allowEmptyResults: true, skipPublishingChecks: true
      sh '''
        bmcval report --reports reports --history "$JENKINS_HOME/bmcval-history/$JOB_NAME" \
          --out reports/dashboard --build "$BUILD_NUMBER" --sha "${GIT_COMMIT:-local}" \
          --firmware "${FIRMWARE_LABEL:-unknown}" || true
      '''
      publishHTML(target: [reportName: 'Validation dashboard', reportDir: 'reports/dashboard',
                           reportFiles: 'index.html', keepAll: true, alwaysLinkToLastBuild: true,
                           allowMissing: true])
      archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
    }
  }
}
