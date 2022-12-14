# Copyright (c) 2012-2017 The ANTLR Project. All rights reserved.
# Use of this file is governed by the BSD 3-clause license that
# can be found in the LICENSE.txt file in the project root.
#/
from antlr4.atn.ATN import ATN
from antlr4.atn.ATNType import ATNType
from antlr4.atn.ATNState import *
from antlr4.atn.Transition import *
from antlr4.atn.LexerAction import *
from antlr4.atn.ATNDeserializationOptions import ATNDeserializationOptions

SERIALIZED_VERSION = 4

class ATNDeserializer (object):

    def __init__(self, options = None):
        if options is None:
            options = ATNDeserializationOptions.defaultOptions
        self.deserializationOptions = options
        self.edgeFactories = None
        self.stateFactories = None
        self.actionFactories = None

    def deserialize(self, data):
        self.data = data
        self.pos = 0
        self.checkVersion()
        atn = self.readATN()
        self.readStates(atn)
        self.readRules(atn)
        self.readModes(atn)
        sets = []
        self.readSets(atn, sets)
        self.readEdges(atn, sets)
        self.readDecisions(atn)
        self.readLexerActions(atn)
        self.markPrecedenceDecisions(atn)
        self.verifyATN(atn)
        if self.deserializationOptions.generateRuleBypassTransitions \
                and atn.grammarType == ATNType.PARSER:
            self.generateRuleBypassTransitions(atn)
            # re-verify after modification
            self.verifyATN(atn)
        return atn

    def checkVersion(self):
        version = self.readInt()
        if version != SERIALIZED_VERSION:
            raise Exception("Could not deserialize ATN with version " + str(version) + " (expected " + str(SERIALIZED_VERSION) + ").")

    def readATN(self):
        grammarType = self.readInt()
        maxTokenType = self.readInt()
        return ATN(grammarType, maxTokenType)

    def readStates(self, atn):
        loopBackStateNumbers = []
        endStateNumbers = []
        nstates = self.readInt()
        for i in range(0, nstates):
            stype = self.readInt()
            # ignore bad type of states
            if stype==ATNState.INVALID_TYPE:
                atn.addState(None)
                continue
            ruleIndex = self.readInt()
            s = self.stateFactory(stype, ruleIndex)
            if stype == ATNState.LOOP_END: # special case
                loopBackStateNumber = self.readInt()
                loopBackStateNumbers.append((s, loopBackStateNumber))
            elif isinstance(s, BlockStartState):
                endStateNumber = self.readInt()
                endStateNumbers.append((s, endStateNumber))

            atn.addState(s)

        # delay the assignment of loop back and end states until we know all the state instances have been initialized
        for pair in loopBackStateNumbers:
            pair[0].loopBackState = atn.states[pair[1]]

        for pair in endStateNumbers:
            pair[0].endState = atn.states[pair[1]]

        numNonGreedyStates = self.readInt()
        for i in range(0, numNonGreedyStates):
            stateNumber = self.readInt()
            atn.states[stateNumber].nonGreedy = True

        numPrecedenceStates = self.readInt()
        for i in range(0, numPrecedenceStates):
            stateNumber = self.readInt()
            atn.states[stateNumber].isPrecedenceRule = True

    def readRules(self, atn):
        nrules = self.readInt()
        if atn.grammarType == ATNType.LEXER:
            atn.ruleToTokenType = [0] * nrules

        atn.ruleToStartState = [0] * nrules
        for i in range(0, nrules):
            s = self.readInt()
            startState = atn.states[s]
            atn.ruleToStartState[i] = startState
            if atn.grammarType == ATNType.LEXER:
                tokenType = self.readInt()
                atn.ruleToTokenType[i] = tokenType

        atn.ruleToStopState = [0] * nrules
        for state in atn.states:
            if not isinstance(state, RuleStopState):
                continue
            atn.ruleToStopState[state.ruleIndex] = state
            atn.ruleToStartState[state.ruleIndex].stopState = state

    def readModes(self, atn):
        nmodes = self.readInt()
        for i in range(0, nmodes):
            s = self.readInt()
            atn.modeToStartState.append(atn.states[s])

    def readSets(self, atn, sets):
        m = self.readInt()
        for i in range(0, m):
            iset = IntervalSet()
            sets.append(iset)
            n = self.readInt()
            containsEof = self.readInt()
            if containsEof!=0:
                iset.addOne(-1)
            for j in range(0, n):
                i1 = self.readInt()
                i2 = self.readInt()
                iset.addRange(Interval(i1, i2 + 1)) # range upper limit is exclusive

    def readEdges(self, atn, sets):
        nedges = self.readInt()
        for i in range(0, nedges):
            src = self.readInt()
            trg = self.readInt()
            ttype = self.readInt()
            arg1 = self.readInt()
            arg2 = self.readInt()
            arg3 = self.readInt()
            trans = self.edgeFactory(atn, ttype, src, trg, arg1, arg2, arg3, sets)
            srcState = atn.states[src]
            srcState.addTransition(trans)

        # edges for rule stop states can be derived, so they aren't serialized
        for state in atn.states:
            for i in range(0, len(state.transitions)):
                t = state.transitions[i]
                if not isinstance(t, RuleTransition):
                    continue
                outermostPrecedenceReturn = -1
                if atn.ruleToStartState[t.target.ruleIndex].isPrecedenceRule:
                    if t.precedence == 0:
                        outermostPrecedenceReturn = t.target.ruleIndex
                trans = EpsilonTransition(t.followState, outermostPrecedenceReturn)
                atn.ruleToStopState[t.target.ruleIndex].addTransition(trans)

        for state in atn.states:
            if isinstance(state, BlockStartState):
                # we need to know the end state to set its start state
                if state.endState is None:
                    raise Exception("IllegalState")
                # block end states can only be associated to a single block start state
                if state.endState.startState is not None:
                    raise Exception("IllegalState")
                state.endState.startState = state

            elif isinstance(state, PlusLoopbackState):
                for i in range(0, len(state.transitions)):
                    target = state.transitions[i].target
                    if isinstance(target, PlusBlockStartState):
                        target.loopBackState = state
            elif isinstance(state, StarLoopbackState):
                for i in range(0, len(state.transitions)):
                    target = state.transitions[i].target
                    if isinstance(target, StarLoopEntryState):
                        target.loopBackState = state

    def readDecisions(self, atn):
        ndecisions = self.readInt()
        for i in range(0, ndecisions):
            s = self.readInt()
            decState = atn.states[s]
            atn.decisionToState.append(decState)
            decState.decision = i

    def readLexerActions(self, atn):
        if atn.grammarType == ATNType.LEXER:
            count = self.readInt()
            atn.lexerActions = [ None ] * count
            for i in range(0, count):
                actionType = self.readInt()
                data1 = self.readInt()
                data2 = self.readInt()
                lexerAction = self.lexerActionFactory(actionType, data1, data2)
                atn.lexerActions[i] = lexerAction

    def generateRuleBypassTransitions(self, atn):

        count = len(atn.ruleToStartState)
        atn.ruleToTokenType = [ 0 ] * count
        for i in range(0, count):
            atn.ruleToTokenType[i] = atn.maxTokenType + i + 1

        for i in range(0, count):
            self.generateRuleBypassTransition(atn, i)

    def generateRuleBypassTransition(self, atn, idx):

        bypassStart = BasicBlockStartState()
        bypassStart.ruleIndex = idx
        atn.addState(bypassStart)

        bypassStop = BlockEndState()
        bypassStop.ruleIndex = idx
        atn.addState(bypassStop)

        bypassStart.endState = bypassStop
        atn.defineDecisionState(bypassStart)

        bypassStop.startState = bypassStart

        excludeTransition = None

        if atn.ruleToStartState[idx].isPrecedenceRule:
            # wrap from the beginning of the rule to the StarLoopEntryState
            endState = None
            for state in atn.states:
                if self.stateIsEndStateFor(state, idx):
                    endState = state
                    excludeTransition = state.loopBackState.transitions[0]
                    break

            if excludeTransition is None:
                raise Exception("Couldn't identify final state of the precedence rule prefix section.")

        else:

            endState = atn.ruleToStopState[idx]

        # all non-excluded transitions that currently target end state need to target blockEnd instead
        for state in atn.states:
            for transition in state.transitions:
                if transition == excludeTransition:
                    continue
                if transition.target == endState:
                    transition.target = bypassStop

        # all transitions leaving the rule start state need to leave blockStart instead
        ruleToStartState = atn.ruleToStartState[idx]
        count = len(ruleToStartState.transitions)
        while count > 0:
            bypassStart.addTransition(ruleToStartState.transitions[count-1])
            del ruleToStartState.transitions[-1]

        # link the new states
        atn.ruleToStartState[idx].addTransition(EpsilonTransition(bypassStart))
        bypassStop.addTransition(EpsilonTransition(endState))

        matchState = BasicState()
        atn.addState(matchState)
        matchState.addTransition(AtomTransition(bypassStop, atn.ruleToTokenType[idx]))
        bypassStart.addTransition(EpsilonTransition(matchState))


    def stateIsEndStateFor(self, state, idx):
        if state.ruleIndex != idx:
            return None
        if not isinstance(state, StarLoopEntryState):
            return None

        maybeLoopEndState = state.transitions[len(state.transitions) - 1].target
        if not isinstance(maybeLoopEndState, LoopEndState):
            return None

        if maybeLoopEndState.epsilonOnlyTransitions and \
                isinstance(maybeLoopEndState.transitions[0].target, RuleStopState):
            return state
        else:
            return None


    #
    # Analyze the {@link StarLoopEntryState} states in the specified ATN to set
    # the {@link StarLoopEntryState#isPrecedenceDecision} field to the
    # correct value.
    #
    # @param atn The ATN.
    #
    def markPrecedenceDecisions(self, atn):
        for state in atn.states:
            if not isinstance(state, StarLoopEntryState):
                continue

            # We analyze the ATN to determine if this ATN decision state is the
            # decision for the closure block that determines whether a
            # precedence rule should continue or complete.
            #
            if atn.ruleToStartState[state.ruleIndex].isPrecedenceRule:
                maybeLoopEndState = state.transitions[len(state.transitions) - 1].target
                if isinstance(maybeLoopEndState, LoopEndState):
                    if maybeLoopEndState.epsilonOnlyTransitions and \
                            isinstance(maybeLoopEndState.transitions[0].target, RuleStopState):
                        state.isPrecedenceDecision = True

    def verifyATN(self, atn):
        if not self.deserializationOptions.verifyATN:
            return
        # verify assumptions
        for state in atn.states:
            if state is None:
                continue

            self.checkCondition(state.epsilonOnlyTransitions or len(state.transitions) <= 1)

            if isinstance(state, PlusBlockStartState):
                self.checkCondition(state.loopBackState is not None)

            if isinstance(state, StarLoopEntryState):
                self.checkCondition(state.loopBackState is not None)
                self.checkCondition(len(state.transitions) == 2)

                if isinstance(state.transitions[0].target, StarBlockStartState):
                    self.checkCondition(isinstance(state.transitions[1].target, LoopEndState))
                    self.checkCondition(not state.nonGreedy)
                elif isinstance(state.transitions[0].target, LoopEndState):
                    self.checkCondition(isinstance(state.transitions[1].target, StarBlockStartState))
                    self.checkCondition(state.nonGreedy)
                else:
                    raise Exception("IllegalState")

            if isinstance(state, StarLoopbackState):
                self.checkCondition(len(state.transitions) == 1)
                self.checkCondition(isinstance(state.transitions[0].target, StarLoopEntryState))

            if isinstance(state, LoopEndState):
                self.checkCondition(state.loopBackState is not None)

            if isinstance(state, RuleStartState):
                self.checkCondition(state.stopState is not None)

            if isinstance(state, BlockStartState):
                self.checkCondition(state.endState is not None)

            if isinstance(state, BlockEndState):
                self.checkCondition(state.startState is not None)

            if isinstance(state, DecisionState):
                self.checkCondition(len(state.transitions) <= 1 or state.decision >= 0)
            else:
                self.checkCondition(len(state.transitions) <= 1 or isinstance(state, RuleStopState))

    def checkCondition(self, condition, message=None):
        if not condition:
            if message is None:
                message = "IllegalState"
            raise Exception(message)

    def readInt(self):
        i = self.data[self.pos]
        self.pos += 1
        return i

    def readInt32(self):
        low = self.readInt()
        high = self.readInt()
        return low | (high << 16)

    def edgeFactory(self, atn, type, src, trg, arg1, arg2, arg3, sets):
        target = atn.states[trg]
        if self.edgeFactories is None:
            ef = [None] * 11
            ef[0] = lambda args : None
            ef[Transition.EPSILON] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                EpsilonTransition(target)
            ef[Transition.RANGE] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                RangeTransition(target, Token.EOF, arg2) if arg3 != 0 else RangeTransition(target, arg1, arg2)
            ef[Transition.RULE] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                RuleTransition(atn.states[arg1], arg2, arg3, target)
            ef[Transition.PREDICATE] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                PredicateTransition(target, arg1, arg2, arg3 != 0)
            ef[Transition.PRECEDENCE] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                PrecedencePredicateTransition(target, arg1)
            ef[Transition.ATOM] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                AtomTransition(target, Token.EOF) if arg3 != 0 else AtomTransition(target, arg1)
            ef[Transition.ACTION] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                ActionTransition(target, arg1, arg2, arg3 != 0)
            ef[Transition.SET] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                SetTransition(target, sets[arg1])
            ef[Transition.NOT_SET] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                NotSetTransition(target, sets[arg1])
            ef[Transition.WILDCARD] = lambda atn, src, trg, arg1, arg2, arg3, sets, target : \
                WildcardTransition(target)
            self.edgeFactories = ef

        if type> len(self.edgeFactories) or self.edgeFactories[type] is None:
            raise Exception("The specified transition type: " + str(type) + " is not valid.")
        else:
            return self.edgeFactories[type](atn, src, trg, arg1, arg2, arg3, sets, target)

    def stateFactory(self, type, ruleIndex):
        if self.stateFactories is None:
            sf = [None] * 13
            sf[ATNState.INVALID_TYPE] = lambda : None
            sf[ATNState.BASIC] = lambda : BasicState()
            sf[ATNState.RULE_START] = lambda : RuleStartState()
            sf[ATNState.BLOCK_START] = lambda : BasicBlockStartState()
            sf[ATNState.PLUS_BLOCK_START] = lambda : PlusBlockStartState()
            sf[ATNState.STAR_BLOCK_START] = lambda : StarBlockStartState()
            sf[ATNState.TOKEN_START] = lambda : TokensStartState()
            sf[ATNState.RULE_STOP] = lambda : RuleStopState()
            sf[ATNState.BLOCK_END] = lambda : BlockEndState()
            sf[ATNState.STAR_LOOP_BACK] = lambda : StarLoopbackState()
            sf[ATNState.STAR_LOOP_ENTRY] = lambda : StarLoopEntryState()
            sf[ATNState.PLUS_LOOP_BACK] = lambda : PlusLoopbackState()
            sf[ATNState.LOOP_END] = lambda : LoopEndState()
            self.stateFactories = sf

        if type> len(self.stateFactories) or self.stateFactories[type] is None:
            raise Exception("The specified state type " + str(type) + " is not valid.")
        else:
            s = self.stateFactories[type]()
            if s is not None:
                s.ruleIndex = ruleIndex
            return s

    def lexerActionFactory(self, type, data1, data2):
        if self.actionFactories is None:
            af = [ None ] * 8
            af[LexerActionType.CHANNEL] = lambda data1, data2: LexerChannelAction(data1)
            af[LexerActionType.CUSTOM] = lambda data1, data2: LexerCustomAction(data1, data2)
            af[LexerActionType.MODE] = lambda data1, data2: LexerModeAction(data1)
            af[LexerActionType.MORE] = lambda data1, data2: LexerMoreAction.INSTANCE
            af[LexerActionType.POP_MODE] = lambda data1, data2: LexerPopModeAction.INSTANCE
            af[LexerActionType.PUSH_MODE] = lambda data1, data2: LexerPushModeAction(data1)
            af[LexerActionType.SKIP] = lambda data1, data2: LexerSkipAction.INSTANCE
            af[LexerActionType.TYPE] = lambda data1, data2: LexerTypeAction(data1)
            self.actionFactories = af

        if type> len(self.actionFactories) or self.actionFactories[type] is None:
            raise Exception("The specified lexer action type " + str(type) + " is not valid.")
        else:
            return self.actionFactories[type](data1, data2)
