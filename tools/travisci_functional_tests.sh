#!/bin/bash

# Copyright (c) 2013 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# This program expects to be run by tox in a virtual python environment
# so that it does not pollute the host development system

sudo_env()
{
    sudo bash -c "PATH=$PATH $*"
}

cleanup()
{
        sudo service memcached stop
        swift-init main stop
        rm -rf ${VIRTUAL_ENV}/etc/swift > /dev/null 2>&1
        rm -rf ${VIRTUAL_ENV}/mnt/gluster-object/test{,2}/* > /dev/null 2>&1
        sudo setfattr -x user.swift.metadata ${VIRTUAL_ENV}/mnt/gluster-object/test{,2} > /dev/null 2>&1
}

quit()
{
        echo "$1"
        exit 1
}


fail()
{
        cleanup
	quit "$1"
}

setupenv()
{
    DIRS="swiftonfile"
    DIRS="${DIRS} etc"
    DIRS="${DIRS} mnt/gluster-object/test"
    DIRS="${DIRS} mnt/gluster-object/test2"
    DIRS="${DIRS} mnt/gluster-object/gsmetadata"
    for newdir in ${DIRS} ; do
        if [ ! -d ${newdir} ] ; then
            mkdir -p ${VIRTUAL_ENV}/${newdir} || fail "Unable to create ${VIRTUAL_ENV}/${newdir}"
        fi
    done
}

### MAIN ###
setupenv

##
## Check the directories exist
##
DIRS="${VIRTUAL_ENV}/mnt/gluster-object ${VIRTUAL_ENV}/mnt/gluster-object/test ${VIRTUAL_ENV}/mnt/gluster-object/test2"
for d in $DIRS ; do
	if [ ! -x $d ] ; then
		quit "$d must exist on an XFS or GlusterFS volume"
	fi
done

##
##
##
export SWIFT_TEST_CONFIG_FILE=${VIRTUAL_ENV}/etc/swift/test.conf


##
## Fix conf files
##
# Install the configuration files
[ -d ${VIRTUAL_ENV}/etc/swift ] || mkdir -p ${VIRTUAL_ENV}/etc/swift > /dev/null 2>&1
cp -r test/functional_auth/tempauth/conf/* \
    ${VIRTUAL_ENV}/etc/swift || fail "Unable to copy configuration files to /etc/swift"
for conffile in account-server.conf container-server.conf object-server.conf ; do
    sed -e "s#/mnt/gluster-object#${VIRTUAL_ENV}/mnt/gluster-object#" \
        ${VIRTUAL_ENV}/etc/swift/${conffile} > ${VIRTUAL_ENV}/etc/swift/${conffile}.edit
    mv ${VIRTUAL_ENV}/etc/swift/${conffile}.edit ${VIRTUAL_ENV}/etc/swift/${conffile}
done

##
## Fix gluster-swift-gen-builders
##
sed -e "s#/etc/swift#${VIRTUAL_ENV}/etc/swift#" \
    bin/gluster-swift-gen-builders > ${VIRTUAL_ENV}/swiftonfile/gluster-swift-gen-builders

##
## Fix Swift code configuration location
##
PYTHONLIBDIR=`python -c "from distutils.sysconfig import get_python_lib; print(get_python_lib())"`
#for swiftfile in manager.py utils.py ; do
for swiftfile in `grep -rl "/etc/swift" ${PYTHONLIBDIR}/swift` ; do
    if ! grep ${VIRTUAL_ENV} ${swiftfile} > /dev/null ; then
        sed -e "s#/etc/swift#${VIRTUAL_ENV}/etc/swift#" \
            ${swiftfile} > ${swiftfile}.edit
        mv ${swiftfile}.edit ${swiftfile}
    fi
done

bash ${VIRTUAL_ENV}/swiftonfile/gluster-swift-gen-builders test test2 || fail "Unable to create ring files"

# Start the services
sudo service memcached start || fail "Unable to start memcached"
sudo_env swift-init main start || fail "Unable to start swift"
#bash --norc -i
#cleanup
#exit 0


mkdir functional_tests > /dev/null 2>&1
nosetests -v --exe \
	--with-xunit \
	--xunit-file functional_tests/gluster-swift-generic-functional-TC-report.xml \
    --with-html-output \
    --html-out-file functional_tests/gluster-swift-generic-functional-result.html \
    test/functional || fail "Functional tests failed"
nosetests -v --exe \
	--with-xunit \
	--xunit-file functional_tests/gluster-swift-functionalnosetests-TC-report.xml \
    --with-html-output \
    --html-out-file functional_tests/gluster-swift-functionalnosetests-result.html \
    test/functionalnosetests || fail "Functional-nose tests failed"

cleanup
exit 0
