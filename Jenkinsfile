// LMA build/publish/deploy pipeline.
//
// Branch -> environment mapping (see D3 in CI-CD-PLAN.md for the open
// question on whether prod adopts the existing LMASA stack or a fresh
// LMASA-prod):
//   qa     -> qa
//   dev01  -> dev01
//   main   -> prod
//
// Any other branch runs build/lint only - publish/deploy are skipped, not
// failed, so feature branches still get CI feedback.
//
// Required per-environment Jenkins credentials (Kind: AWS Credentials):
//   aws-lma-qa, aws-lma-dev01, aws-lma-prod
// each scoped to deploy only its own LMASA-<env> stack.
//
// Required per-environment parameter files (not yet created - see the
// follow-up task): deploy/params/lma-<env>.json
//
// The 4 NoEcho secret parameters (TavilyApiKey, ElevenLabsApiKey,
// SimliApiKey, ZoomMeetingSdkClientSecret) are NOT set here and must not be
// added to the parameter files as plaintext. They resolve at deploy time via
// CloudFormation dynamic references (e.g.
// {{resolve:secretsmanager:lma/<env>/tavily-api-key}}) written directly into
// the parameter file - Jenkins never sees the plaintext value.
//
// To trigger a build: in Jenkins, open this branch's job and click "Build
// with Parameters" - pick ACTION=DEPLOY (default) for the normal
// build/publish/deploy flow, or ACTION=DESTROY to instead delete that
// branch's environment stack (pauses for a typed confirmation first - see
// the Destroy stage below).
//
// The whole pipeline runs inside the image built from ci/Dockerfile (see
// that file) rather than directly on the Jenkins server - the server only
// needs Docker itself; every build tool (node, make, zip, python, aws cli,
// sam cli) lives in that image instead of being hand-installed on the host.
// Requires the "Docker Pipeline" Jenkins plugin. The docker.sock mount lets
// steps inside the container drive the host's Docker daemon (needed for the
// SAM/container build steps) without needing Docker-in-Docker.

def ENV_MAP = [
    'qa'   : 'qa',
    'dev01': 'dev01',
    'main' : 'prod',
]

pipeline {
    agent {
        dockerfile {
            filename 'ci/Dockerfile'
            dir '.'
            // --group-add 988: the docker.sock on this host is owned by
            // root:988 (confirmed via the Check Environment diagnostic -
            // `ls -la /var/run/docker.sock`), and the container otherwise
            // runs as a jenkins-mapped uid/gid that isn't a member of that
            // group. Without this, every `docker` command inside the
            // container fails with "permission denied" even though the
            // socket is mounted correctly. If docker.sock's group ever
            // changes on this host, this number needs to change with it.
            args '-v /var/run/docker.sock:/var/run/docker.sock --group-add 988'
        }
    }

    options {
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    // Only shows up in Jenkins as "Build with Parameters" once this block
    // has run at least once on a branch (first run uses the default).
    // DESTROY is deliberately not the default - every run has to pick it
    // on purpose.
    parameters {
        choice(
            name: 'ACTION',
            choices: ['DEPLOY', 'DESTROY'],
            description: 'DEPLOY runs the normal build/publish/deploy pipeline (default). DESTROY skips build/publish and instead deletes this branch\'s environment stack - asks for manual confirmation first.'
        )
    }

    environment {
        REGION = 'us-east-1'
        CFN_BUCKET_BASENAME = 'lma-artifacts'
        CFN_PREFIX = 'lma'
    }

    stages {

        stage('Validate Branch Mapping') {
            steps {
                script {
                    env.DEPLOY_ENV = ENV_MAP[env.BRANCH_NAME]
                    if (env.DEPLOY_ENV) {
                        echo "Branch '${env.BRANCH_NAME}' maps to environment '${env.DEPLOY_ENV}' - publish/deploy will run."
                    } else {
                        echo "Branch '${env.BRANCH_NAME}' has no environment mapping - build/lint only, publish/deploy will be skipped."
                    }
                }
            }
        }

        stage('Check Environment') {
            steps {
                sh '''
                    echo "Checking required build tools on this agent (jenkins user, jenkins PATH)..."
                    MISSING=0
                    for cmd in bash node npm docker zip python3 pip3 virtualenv aws sam make; do
                        if command -v "$cmd" >/dev/null 2>&1; then
                            echo "OK       $cmd -> $(command -v $cmd) ($($cmd --version 2>&1 | head -1))"
                        else
                            echo "MISSING  $cmd"
                            MISSING=1
                        fi
                    done
                    if [ "$MISSING" -eq 1 ]; then
                        echo "One or more required tools are missing on this Jenkins agent - see CLAUDE.md for the full prerequisite list."
                        exit 1
                    fi

                    echo ""
                    echo "-- Docker socket access (diagnostic, non-fatal) --"
                    echo "Running as: $(id)"
                    ls -la /var/run/docker.sock 2>&1 || echo "socket not found at that path"
                    docker info >/tmp/docker_info.log 2>&1 && echo "docker info: OK" || {
                        echo "docker info: FAILED"
                        cat /tmp/docker_info.log
                    }
                '''
            }
        }

        stage('Setup CLI') {
            when { expression { params.ACTION == 'DEPLOY' } }
            steps {
                // setup-cli alone assumes .venv already exists - setup-python
                // is what actually creates it (make setup-cli's own error,
                // "No such file or directory" on .venv/bin/pip, is exactly
                // this - the venv was never created first).
                sh 'make setup-python setup-cli'
            }
        }

        stage('Build') {
            when { expression { params.ACTION == 'DEPLOY' } }
            steps {
                sh 'make build'
            }
        }

        stage('Publish') {
            when { allOf { expression { env.DEPLOY_ENV != null }; expression { params.ACTION == 'DEPLOY' } } }
            steps {
                // Needs AWS credentials too - it uploads to S3 and may call
                // other AWS APIs while packaging (missed this originally;
                // only Deploy/Verify/Export had credentials wired in).
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    // Jenkins' Docker Pipeline plugin does not reliably carry
                    // a self-referencing PATH from the top-level environment{}
                    // block into the container (confirmed - it showed up as
                    // the plain default PATH, no .venv/bin). Activating the
                    // venv directly in this one shell step sidesteps that
                    // entirely - `.` runs in the current shell, so
                    // publish.sh right after it inherits the activated PATH.
                    //
                    // AWS_DEFAULT_REGION is set the same explicit way, for
                    // the same reason: some AWS calls inside the publish
                    // process don't receive an explicit --region and fall
                    // back to this env var ("No region information found"),
                    // and top-level environment{} values aren't reliably
                    // visible in here either.
                    // Calling `lma publish` directly instead of going through
                    // publish.sh - the wrapper script only forwards its 3 fixed
                    // positional args (bucket-basename/prefix/region), it doesn't
                    // pass through anything else, and this needs --force too:
                    // its checksum-based "skip if unchanged" caching produced a
                    // real bug on this brand-new bucket - it skipped
                    // re-uploading lma-websocket-transcriber-stack's template.yaml
                    // (comparing against a stale local checksum from a build
                    // against a different bucket) even though this bucket had
                    // never received it, leaving the deploy with a dangling
                    // nested-stack reference to a file that was never uploaded.
                    sh """
                        . .venv/bin/activate
                        export AWS_DEFAULT_REGION=${REGION}
                        lma publish --bucket-basename ${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV} --prefix ${CFN_PREFIX} --region ${REGION} --force
                    """
                }
            }
        }

        stage('Deploy') {
            when { allOf { expression { env.DEPLOY_ENV != null }; expression { params.ACTION == 'DEPLOY' } } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh """
                        # `aws cloudformation deploy` has no --template-url flag -
                        # --template-file only accepts a local path (confirmed via
                        # `aws cloudformation deploy help`). The local lma-main.yaml
                        # in the workspace still has literal placeholder tokens
                        # (e.g. <REGION_TOKEN>) in its nested-stack TemplateURLs -
                        # those only get substituted during publish.sh's render,
                        # producing the copy it already uploaded to S3. So: pull
                        # that already-rendered copy back down locally first, then
                        # deploy from it instead of the raw workspace file.
                        aws s3 cp s3://${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV}-${REGION}/${CFN_PREFIX}/lma-main.yaml ./lma-main-rendered.yaml

                        aws cloudformation deploy \
                          --region ${REGION} \
                          --stack-name LMASA-${env.DEPLOY_ENV} \
                          --template-file ./lma-main-rendered.yaml \
                          --s3-bucket ${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV}-${REGION} \
                          --parameter-overrides file://deploy/params/lma-${env.DEPLOY_ENV}.json \
                          --role-arn arn:aws:iam::528757797189:role/LMASA-${env.DEPLOY_ENV}-cfn-service-role \
                          --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
                          --no-fail-on-empty-changeset
                    """
                }
            }
        }

        stage('Verify') {
            when { allOf { expression { env.DEPLOY_ENV != null }; expression { params.ACTION == 'DEPLOY' } } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh """
                        STATUS=\$(aws cloudformation describe-stacks \
                          --region ${REGION} \
                          --stack-name LMASA-${env.DEPLOY_ENV} \
                          --query 'Stacks[0].StackStatus' --output text)
                        echo "Stack status: \$STATUS"
                        case "\$STATUS" in
                          CREATE_COMPLETE|UPDATE_COMPLETE) exit 0 ;;
                          *) echo "Stack did not reach a healthy COMPLETE status"; exit 1 ;;
                        esac
                    """
                }
            }
        }

        stage('Export Outputs to SSM') {
            when { allOf { expression { env.DEPLOY_ENV != null }; expression { params.ACTION == 'DEPLOY' } } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh "./scripts/export_stack_resources_to_ssm.sh LMASA-${env.DEPLOY_ENV} ${env.DEPLOY_ENV} ${REGION} ${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV}-${REGION}"
                }
            }
        }

        stage('Destroy') {
            when { allOf { expression { env.DEPLOY_ENV != null }; expression { params.ACTION == 'DESTROY' } } }
            steps {
                // Typed confirmation, not just a Proceed/Abort click - a
                // misclick on a button is easy, misclicking AND typing the
                // exact stack name is not. This pauses the pipeline in
                // Jenkins' UI until someone with approval rights fills it in.
                script {
                    def confirmation = input(
                        message: "This permanently deletes LMASA-${env.DEPLOY_ENV} and everything in it (data, recordings, user pool - no undo). Type the environment name to confirm.",
                        parameters: [string(name: 'CONFIRM_ENV_NAME', defaultValue: '', trim: true)]
                    )
                    if (confirmation != env.DEPLOY_ENV) {
                        error "Typed '${confirmation}' does not match environment '${env.DEPLOY_ENV}' - aborting destroy."
                    }
                }
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh """
                        aws cloudformation delete-stack --region ${REGION} --stack-name LMASA-${env.DEPLOY_ENV}
                        echo "Delete initiated - waiting for it to finish (this can take 15-30+ minutes for a full stack)..."
                        aws cloudformation wait stack-delete-complete --region ${REGION} --stack-name LMASA-${env.DEPLOY_ENV}
                        echo "LMASA-${env.DEPLOY_ENV} deleted."
                    """
                }
            }
        }
    }

    post {
        always {
            echo "Pipeline finished for branch ${env.BRANCH_NAME} (environment: ${env.DEPLOY_ENV ?: 'none - build/lint only'})"
        }
    }
}
