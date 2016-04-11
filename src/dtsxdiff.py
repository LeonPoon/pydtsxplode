# Copyright 2016 Leon Poon and Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import difflib
import os
import string
import sys
from collections import namedtuple
from difflib import unified_diff
from functools import partial
from inspect import getargspec

from pydtsxplode import DtsxExploder, DtsxComponent
from xmlxplode import BOM_MAP
from xmlxplode.fs.inmem import InMemFs


class DtsxExploderForDiff(DtsxExploder):

    DEFAULT_ID = '{........-....-....-....-............}'

    def getDom(self, source):
        dom = super(DtsxExploderForDiff, self).getDom(source)
        m = dict()
        self.collectDtsId(dom.documentElement, m)
        self.replaceDtsId(dom.documentElement, m)
        return dom

    def replaceDtsId(self, elem, m):

        if elem.attributes:
            for i in range(len(elem.attributes)):
                attr = elem.attributes.item(i)
                if attr.value in m:
                    attr.value = m[attr.value]
        if elem.childNodes:
            for elem in elem.childNodes:
                self.replaceDtsId(elem, m)

    def collectDtsId(self, elem, m):

        if callable(getattr(elem, 'getAttributeNodeNS', None)):
            attr = elem.getAttributeNodeNS(DtsxComponent.defaultNamespaceURI, 'DTSID')
            if attr:
                id = attr.value
                if id in m:
                    raise ValueError('%s: %s' % (id, m))
                else:
                    refId = elem.getAttributeNS(DtsxComponent.defaultNamespaceURI, 'refId')
                    m[id] = refId
                    attr.value = refId

        if elem.childNodes:
            for elem in elem.childNodes:
                self.collectDtsId(elem, m)


def getSource(filename):
    if filename == '-':
        source = sys.stdin
    else:
        dtsxFile = open(filename, 'rb')
        try:
            source = dtsxFile.read()
        finally:
            dtsxFile.close()
    return filename, source


def splitForDiff(s):
    s = s.split('\n')
    return [x + '\n' for x in s[:-1]] + ([s[-1]] if s[-1] else [])


default_unified = getargspec(unified_diff)
default_unified = dict(zip(default_unified.args[-len(default_unified.defaults):], default_unified.defaults))
default_unified = default_unified['n']


def genDiff(s1, s2, l, r, unified=default_unified, linesep=os.linesep):
    s1 = splitForDiff(s1)
    s2 = splitForDiff(s2)
    for line in unified_diff(s1, s2, l, r, n=unified):
        yield line
        if not line.endswith('\n'):
            yield(linesep)
            yield('\ No newline at end of file')
            yield(linesep)


def isPrintable(s):
    if not s:
        return s
    if isinstance(s, unicode):
        return s
    for x in BOM_MAP:
        if s.startswith(x):
            s = s[len(x):]
            s, _ = BOM_MAP[x].decode(s)
            return s
    if all(c in string.printable or c in '\r\n\t' for c in s):
        return s
    return None


def printDiff(diffObj, unified=default_unified, out=sys.stdout, sep=os.sep, linesep=os.linesep):
    l, r = diffObj
    if not l or not r:
        parts, (typ, content) = l or r
        if typ == 'a regular file':
            if content:
                diffObj = (l or EMPTY_DiffInfo, r or EMPTY_DiffInfo)
            else:
                out.write('Only in %s: %s' % (sep.join(parts[:-1]), parts[-1]))
                out.write(linesep)
                return
        else:
            return

    (l, (tl, s1)), (r, (tr, s2)) = diffObj
    l = sep.join(l)
    r = sep.join(r)
    if tl != tr:
        out.write('File %s is %s while file %s is %s' % (l, tl, r, tr))
        out.write(linesep)
    else:
        s1 = isPrintable(s1)
        s2 = isPrintable(s2)
        if s1 is None or s2 is None:
            out.write('Binary files %s and %s differ' % (l, r))
            out.write(linesep)
        else:
            map(out.write, genDiff(s1, s2, l, r, unified, linesep))



Diff = namedtuple('Diff', 'left, right')
DiffComp = namedtuple('DiffComp', 'parts, inf')
DiffInfo = namedtuple('DiffInfo', 'type, content')
EMPTY_DiffInfo = DiffComp(('/dev/null',), DiffInfo('a regular file', ''))


def findDiffs(fn1, fs1, fn2, fs2):
    diffs = []
    names = set(fs1.keys() if fs1 else []).union(fs2.keys() if fs2 else [])
    names = [(n, (fs1 and fs1.nodeType(n), fs2 and fs2.nodeType(n))) for n in names]
    names.sort()
    for name, (l, r) in names:
        if l == r:
            if l == 'a regular file':
                l = DiffInfo(l, fs1[name])
                r = DiffInfo(r, fs2[name])
                if l != r:
                    name = [name]
                    diffs.append(Diff(DiffComp(fn1+name, l), DiffComp(fn2+name, r)))
            elif l == 'a directory':
                diffs = diffs + findDiffs(fn1 + [name], fs1[name], fn2 + [name], fs2[name])
            else:
                raise ValueError(l)
        else:
            if l and r:  # not same type
                diffs.append(Diff(DiffComp(fn1 + [name], DiffInfo(l, None)), DiffComp(fn2 + [name], DiffInfo(r, None))))
            else:  # one side missing
                typ = l or r
                if l and fs1:
                    l = DiffComp(fn1 + [name], DiffInfo(l, fs1[name]))
                if r and fs2:
                    r = DiffComp(fn2 + [name], DiffInfo(r, fs2[name]))
                diffs.append(Diff(l, r))
                if typ == 'a directory':
                    diffs += findDiffs(fn1 + [name], fs1 and fs1[name], fn2 + [name], fs2 and fs2[name])
    return diffs



def main((opts, (source1, source2)), out=sys.stdout):
    fn1, source1 = getSource(source1)
    fn2, source2 = getSource(source2)
    if source1 != source2:
        fs1 = InMemFs()
        DtsxExploderForDiff.explode(source1, fs1, dtsxName='Package')
        fs2 = InMemFs()
        DtsxExploderForDiff.explode(source2, fs2, dtsxName='Package')
        map(partial(printDiff, unified=opts['unified'], out=out), findDiffs([fn1], fs1, [fn2], fs2))



def parseOpts(argv):
    cmd = argv[0]
    argv = argv[1:] # first arg is ourself
    import getopt
    optlist, args = getopt.getopt(argv, 'U:', ['unified='])
    opts = {
        'unified': default_unified,
    }
    for o, a in optlist:  # @UnusedVariable
        if o == '-U' or o == '--unified':
            opts['unified'] = int(a)
        else:
            assert False, '%s: unrecognized option %r' % (cmd, o)
    return opts, args


if __name__ == '__main__':
    import sys
    main(parseOpts(sys.argv))
