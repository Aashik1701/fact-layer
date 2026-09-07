import React, { useState } from 'react';
import { AppShell } from '@/components/layout/AppShell';
import { NavTab } from '@/components/layout/Sidebar';
import { OverviewPage } from '@/components/overview/OverviewPage';
import { RelationsPage } from '@/components/relations/RelationsPage';
import { FactsPage } from '@/components/facts/FactsPage';
import { DocumentsPage } from '@/components/documents/DocumentsPage';
import { RequiredCasesPage } from '@/components/cases/RequiredCasesPage';
import { RejectedFactsPage } from '@/components/rejected/RejectedFactsPage';
import { ClustersPage } from '@/components/clusters/ClustersPage';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<NavTab>('overview');

  const renderContent = () => {
    switch (currentTab) {
      case 'overview':
        return (
          <OverviewPage
            onNavigateToCases={() => setCurrentTab('cases')}
            onNavigateToRelations={() => setCurrentTab('relations')}
            onNavigateToFacts={() => setCurrentTab('facts')}
          />
        );
      case 'relations':
        return <RelationsPage />;
      case 'facts':
        return <FactsPage />;
      case 'documents':
        return (
          <DocumentsPage
            onNavigateToFacts={(_docId) => setCurrentTab('facts')}
          />
        );
      case 'cases':
        return (
          <RequiredCasesPage
            onNavigateToIngest={() => setCurrentTab('documents')}
          />
        );
      case 'rejected':
        return <RejectedFactsPage />;
      case 'clusters':
        return (
          <ClustersPage
            onSelectCluster={(_c) => setCurrentTab('facts')}
          />
        );
      default:
        return (
          <OverviewPage
            onNavigateToCases={() => setCurrentTab('cases')}
            onNavigateToRelations={() => setCurrentTab('relations')}
            onNavigateToFacts={() => setCurrentTab('facts')}
          />
        );
    }
  };

  return (
    <AppShell currentTab={currentTab} onTabChange={(tab) => setCurrentTab(tab)}>
      {renderContent()}
    </AppShell>
  );
};

export default App;
