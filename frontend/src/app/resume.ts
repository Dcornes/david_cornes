export interface SkillGroup {
  label: string;
  items: string[];
}

export interface Job {
  title: string;
  company: string;
  location: string;
  dates: string;
  bullets: string[];
}

export interface Degree {
  school: string;
  degree: string;
  date: string;
}

export interface Resume {
  name: string;
  title: string;
  summary: string;
  location: string;
  email: string;
  phone: string;
  linkedin: string;
  skills: SkillGroup[];
  experience: Job[];
  education: Degree[];
}

export const RESUME: Resume = {
  name: 'David Cornes',
  title: 'Software Engineer',
  summary:
    'A Chicago-based software engineer with a background in trading. I build reliable backend systems, APIs and compliance tooling for trading firms and crypto exchanges.',
  location: 'Chicago, IL',
  email: 'dcornesjr@gmail.com',
  phone: '(312) 771-3328',
  linkedin: 'https://www.linkedin.com/in/dcornesjr',
  skills: [
    {
      label: 'Languages',
      items: ['Python', 'C#', 'Java', 'SQL', 'C++', 'Git', 'Linux', 'Angular', 'TypeScript', 'C', 'Scala', 'HTML', 'CSS', 'R'],
    },
    {
      label: 'Data',
      items: ['SQL', 'MySQL', 'RabbitMQ', 'InfluxDB', 'Snowflake', 'Pandas'],
    },
    {
      label: 'Software',
      items: ['BitBucket', 'GitLab', 'Jira', 'IntelliJ', 'Visual Studio', 'PyCharm', 'DataGrip', 'Vim', 'Grafana'],
    },
  ],
  experience: [
    {
      title: 'Software Engineer',
      company: 'AlphaPoint',
      location: 'Remote',
      dates: 'Sept 2022 – Feb 2024',
      bullets: [
        'Refactored and added new functionality to an application that provides liquidity to rising crypto exchanges with thousands of end users',
        'Designed REST and Websocket APIs to increase efficiency and speed from the previous WinForms design',
        'Integrated with third-party APIs to expand data sources and variety of liquidity',
        'Incorporated tools such as Grafana and InfluxDB to improve monitoring to decrease incident response time',
        'Redesigned backend data schemas for improved data storage for compliance, operations and accounting purposes',
        'Collaborated with product managers and other engineers to translate business requirements into reliable technical solutions',
      ],
    },
    {
      title: 'Associate Software Engineer',
      company: 'Chicago Trading Company',
      location: 'Chicago, IL',
      dates: 'April 2021 – Mar 2022',
      bullets: [
        'Constructed full-stack features for high traffic applications which reduced operational burden and outtrade risk',
        'Coordinated multiple teams and vendors to build FINRA Consolidated Audit Trail application approaching 0% error rate and outperforming industry error rate fivefold',
        'Architected compliance long-term file storage framework which guaranteed future ease of accessibility as well as reliable audit WORM storage',
        'Stood up new FIX messaging stream for an intraday trade reconciliation application',
      ],
    },
    {
      title: 'Software Engineering Intern',
      company: 'Chicago Trading Company',
      location: 'Chicago, IL',
      dates: 'July 2020 – Sept 2020',
      bullets: [
        'Wrote new feature in the Fitting team’s primary tool to calculate and display diffs between fits',
        'Used agile methodology to coordinate with traders and the Fitting team to determine requirements and scope of work',
      ],
    },
    {
      title: 'S&P Clerk',
      company: 'Chicago Trading Company',
      location: 'Chicago, IL',
      dates: 'April 2019 – April 2021',
      bullets: ['Trading operations', 'Full-stack side project'],
    },
    {
      title: 'Trading Assistant',
      company: 'Prime Trading',
      location: 'Chicago, IL',
      dates: 'June 2014 – Jan 2019',
      bullets: [],
    },
    {
      title: 'Grain Trading Assistant',
      company: 'Prime Trading',
      location: 'Chicago, IL',
      dates: 'Aug 2012 – June 2014',
      bullets: [],
    },
    {
      title: 'Junior Trader',
      company: 'HTG Capital Partners',
      location: 'Chicago, IL',
      dates: 'Feb 2012 – May 2012',
      bullets: [],
    },
  ],
  education: [
    { school: 'DePaul University', degree: 'MS in Computer Science', date: 'June 2021' },
    { school: 'University of Montana', degree: 'BS in Economics', date: 'May 2010' },
  ],
};
